"""Resolve opt-in CI names and enforce repository ownership before Azure writes."""

import argparse
import json
import os
import re
import subprocess
import sys

MARKERS = ("ci_namespace", "ci_repository_id")


def settings():
    namespace = os.environ.get("CI_NAMESPACE", "")
    repository_id = os.environ.get("GITHUB_REPOSITORY_ID", "")
    if namespace and (not re.fullmatch(r"[a-z]{2,3}", namespace) or namespace == "tre"):
        raise ValueError("CI_NAMESPACE must contain two or three lowercase letters other than tre.")
    if namespace and not re.fullmatch(r"[1-9][0-9]*", repository_id):
        raise ValueError("Namespaced CI requires GITHUB_REPOSITORY_ID.")
    return namespace, repository_id


def owns(tags):
    namespace, repository_id = settings()
    tags = tags or {}
    if not isinstance(tags, dict):
        raise ValueError("Invalid resource ownership tags.")
    if not namespace:
        return not any(key in tags for key in MARKERS)
    return tags.get("ci_namespace") == namespace and tags.get("ci_repository_id") == repository_id


def group_pattern():
    namespace, _ = settings()
    return rf"rg-{namespace or 'tre'}[0-9a-f]{{8}}(?:-mgmt)?"


def azure(*args):
    subscription = os.environ.get("AZURE_SUBSCRIPTION_ID") or os.environ.get("ARM_SUBSCRIPTION_ID", "")
    if not subscription:
        raise ValueError("The CI subscription is missing.")
    return json.loads(
        subprocess.check_output(["az", *args, "--subscription", subscription, "-o", "json"], text=True, timeout=90)
    )


def verify_groups(groups, core, ref):
    """Refuse adoption of existing roots; descendants belong to verified roots."""
    if not isinstance(groups, list):
        raise ValueError("Invalid resource group inventory.")
    roots = {core, core + "-mgmt"}
    root_exists = any(isinstance(group, dict) and group.get("name") in roots for group in groups)
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("name"), str):
            raise ValueError("Invalid resource group identity.")
        name = group["name"]
        if name.startswith((core + "-ws-", core + "-svc-")):
            tags = group.get("tags") or {}
            if not root_exists or (any(key in tags for key in MARKERS) and not owns(tags)):
                raise ValueError(f"Resource group {name} has no matching CI root ownership.")
        if name not in (core, core + "-mgmt"):
            continue
        tags = group.get("tags") or {}
        if not owns(tags) or (settings()[0] and tags.get("ci_git_ref") != ref):
            raise ValueError(f"Resource group {name} does not belong to this CI environment.")


def verify_deployment():
    namespace, _ = settings()
    if not namespace:
        return
    from ci_environment_id import environment_id

    ref = os.environ.get("TF_VAR_ci_git_ref", "")
    suffix = environment_id(
        ref, os.environ.get("LOCATION", ""), os.environ.get("AZURE_ENVIRONMENT", ""), namespace, settings()[1]
    )
    tre_id = namespace + suffix
    expected = {
        "TRE_ID": tre_id,
        "TF_VAR_mgmt_resource_group_name": f"rg-{tre_id}-mgmt",
        "TF_VAR_mgmt_storage_account_name": tre_id + "mgmt",
        "ACR_NAME": tre_id,
    }
    for key, value in expected.items():
        if os.environ.get(key) != value:
            raise ValueError(f"{key} does not match the repository CI identity.")
    verify_groups(azure("group", "list"), "rg-" + tre_id, ref)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("resolve", "verify-deployment", "verify-destroy", "verify-legacy-main"))
    parser.add_argument("--group")
    args = parser.parse_args()
    try:
        namespace, repository_id = settings()
        if args.operation == "resolve":
            if not namespace:
                return 0
            from ci_environment_id import environment_id

            suffix = environment_id(
                os.environ["CI_GIT_REF"],
                os.environ.get("CI_LOCATION", ""),
                os.environ.get("CI_AZURE_ENVIRONMENT", ""),
                namespace,
                repository_id,
            )
            tre_id = namespace + suffix
            values = {
                "tre_id": tre_id,
                "acr_name": tre_id,
                "mgmt_resource_group_name": f"rg-{tre_id}-mgmt",
                "mgmt_storage_account_name": tre_id + "mgmt",
                "encryption_kv_name": tre_id + "mgmt",
                "devcontainer_tag": tre_id,
            }
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                for key, value in values.items():
                    output.write(f"{key}={value}\n")
        elif args.operation == "verify-deployment":
            verify_deployment()
        elif args.operation == "verify-legacy-main":
            name = os.environ.get("MAIN_TRE_ID", "")
            if namespace or not re.fullmatch(r"[a-zA-Z0-9-]+", name):
                raise ValueError("Invalid legacy main environment.")
            roots = [g for g in azure("group", "list") if g.get("name") in ("rg-" + name, "rg-" + name + "-mgmt")]
            if not roots or not all(owns(g.get("tags")) for g in roots):
                raise ValueError("Legacy main cleanup cannot manage namespaced or unowned roots.")
        else:
            if not args.group or not re.fullmatch(group_pattern(), args.group):
                raise ValueError("Invalid CI destruction target.")
            ref = (
                os.environ.get("CI_GIT_REF")
                or os.environ.get("CI_CLEANUP_REF")
                or os.environ.get("TF_VAR_ci_git_ref", "")
            )
            groups = azure("group", "list")
            verify_groups(groups, args.group.removesuffix("-mgmt"), ref)
            if namespace:
                roots = [g for g in groups if g.get("name") in (args.group, args.group + "-mgmt")]
                if not roots:
                    raise ValueError("No owned root exists to authorise descendant deletion.")
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"ERROR: CI ownership check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
