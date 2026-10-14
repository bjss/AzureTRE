"""Exercise repository boundaries without Azure access."""

from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ci_environment_id import environment_id  # noqa: E402
import ci_repository_scope as scope  # noqa: E402
import ci_cleanup_scope as cleanup  # noqa: E402
import destroy_ci_ref_env as destroy  # noqa: E402

REF = "refs/pull/5252/merge"


class RepositoryScopeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CI_NAMESPACE": "cgi", "GITHUB_REPOSITORY_ID": "1416717883"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tags = {"ci_namespace": "cgi", "ci_repository_id": "1416717883", "ci_git_ref": REF}
        self.groups = [
            {"name": name, "id": f"/subscriptions/sub/resourceGroups/{name}", "tags": deepcopy(self.tags)}
            for name in ("rg-cgi12345678", "rg-cgi12345678-mgmt")
        ]

    def test_names_separate_repositories_and_survive_organisation_rename(self):
        first = environment_id(REF, "switzerlandnorth", namespace="cgi", repository_id="1416717883")
        other = environment_id(REF, "switzerlandnorth", namespace="cgi", repository_id="344511263")
        self.assertNotEqual(first, other)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "CGI/AzureTRE"}):
            self.assertEqual(
                first, environment_id(REF, "switzerlandnorth", namespace="cgi", repository_id="1416717883")
            )
        self.assertNotEqual(first, environment_id(REF, "switzerlandnorth"))

    def test_legacy_id_is_unchanged_and_main_requires_namespace(self):
        self.assertEqual(environment_id(REF, "switzerlandnorth"), "0bcf9e77")
        with self.assertRaises(ValueError):
            environment_id("refs/heads/main", "switzerlandnorth")
        self.assertEqual(
            len(environment_id("refs/heads/main", "switzerlandnorth", namespace="cgi", repository_id="1")), 8
        )

    def test_invalid_namespace_and_missing_repository_are_refused(self):
        for namespace in ("tre", "CGI", "c-g", "toolong", "cgi\n"):
            with (
                self.subTest(namespace=namespace),
                patch.dict(os.environ, {"CI_NAMESPACE": namespace}),
                self.assertRaises(ValueError),
            ):
                scope.settings()
        with patch.dict(os.environ, {"GITHUB_REPOSITORY_ID": ""}), self.assertRaises(ValueError):
            scope.settings()

    def test_legacy_cleanup_skips_complete_and_partial_ownership_markers(self):
        with patch.dict(os.environ, {"CI_NAMESPACE": ""}):
            self.assertTrue(scope.owns({"ci_git_ref": REF}))
            for tags in (self.tags, {"ci_namespace": ""}, {"ci_repository_id": ""}):
                self.assertFalse(scope.owns(tags))
            legacy = deepcopy(self.groups)
            for group in legacy:
                group["name"] = group["name"].replace("cgi", "tre")
                group["id"] = group["id"].replace("cgi", "tre")
            self.assertEqual(destroy.targets(legacy, REF, "sub"), [])

    def test_foreign_or_missing_tags_cannot_be_adopted(self):
        for tags in (
            {},
            {**self.tags, "ci_repository_id": "344511263"},
            {**self.tags, "ci_namespace": "abc"},
            {**self.tags, "ci_git_ref": "refs/heads/main"},
        ):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                scope.verify_groups([{**self.groups[0], "tags": tags}], "rg-cgi12345678", REF)
        scope.verify_groups([], "rg-cgi12345678", REF)
        scope.verify_groups(self.groups, "rg-cgi12345678", REF)

    def test_destruction_requires_matching_siblings(self):
        self.assertEqual(destroy.targets(self.groups, REF, "sub"), ["rg-cgi12345678"])
        self.groups[1]["tags"]["ci_repository_id"] = "344511263"
        with self.assertRaises(ValueError):
            destroy.targets(self.groups, REF, "sub")

    def test_cleanup_inventory_preserves_foreign_sibling_as_rejection(self):
        self.groups[1]["tags"]["ci_repository_id"] = "344511263"
        with (
            patch.object(cleanup, "verify_account", return_value="sub"),
            patch.object(cleanup.subprocess, "check_output") as read,
        ):
            import json

            read.return_value = json.dumps(self.groups)
            inventory = cleanup.inventory()
        self.assertIsNone(inventory["rg-cgi12345678-mgmt"])
        with self.assertRaises(cleanup.RecoveryError):
            cleanup.verify_target(inventory, REF, "rg-cgi12345678")

    def test_namespaced_recovery_checks_generated_backend_and_owner(self):
        import recover_bootstrap_lease as recovery
        from test_recover_bootstrap_lease import ENV

        env = {**ENV, "CI_NAMESPACE": "cgi", "GITHUB_REPOSITORY_ID": "1416717883"}
        suffix = environment_id(
            env["TF_VAR_ci_git_ref"], "switzerlandnorth", namespace="cgi", repository_id="1416717883"
        )
        env.update(
            CI_ENVIRONMENT_ID=suffix,
            TF_VAR_location="switzerlandnorth",
            AZURE_ENVIRONMENT="AzureCloud",
            TF_VAR_mgmt_resource_group_name="rg-cgi" + suffix + "-mgmt",
            TF_VAR_mgmt_storage_account_name="cgi" + suffix + "mgmt",
        )
        with patch.dict(os.environ, env, clear=True):
            ctx = recovery.context()
            group = {
                "id": f"/subscriptions/{ctx['subscription']}/resourceGroups/{ctx['group']}",
                "tags": {**self.tags, "ci_git_ref": ctx["ref"]},
            }
            with patch.object(recovery, "azure", return_value=group):
                recovery.verify_owner(ctx)
                group["tags"]["ci_repository_id"] = "344511263"
                with self.assertRaises(recovery.RecoveryError):
                    recovery.verify_owner(ctx)

    def test_orphaned_or_foreign_descendants_are_refused(self):
        orphan = {"name": "rg-cgi12345678-ws-test", "tags": {}}
        with self.assertRaises(ValueError):
            scope.verify_groups([orphan], "rg-cgi12345678", REF)
        scope.verify_groups([*self.groups, orphan], "rg-cgi12345678", REF)
        foreign = {**orphan, "tags": {**self.tags, "ci_repository_id": "344511263"}}
        with self.assertRaises(ValueError):
            scope.verify_groups([*self.groups, foreign], "rg-cgi12345678", REF)

    def test_copied_resource_configuration_fails_before_azure_access(self):
        with (
            patch.dict(os.environ, {"TF_VAR_ci_git_ref": REF, "LOCATION": "switzerlandnorth", "TRE_ID": "upstream"}),
            patch.object(scope, "azure") as azure,
            self.assertRaises(ValueError),
        ):
            scope.verify_deployment()
        azure.assert_not_called()

    def test_mask_escapes_workflow_commands(self):
        log = StringIO()
        with redirect_stdout(log):
            scope.mask("synthetic%\r\n::warning::value")
        self.assertEqual(log.getvalue(), "::add-mask::synthetic%25%0D%0A::warning::value\n")

    def test_masks_are_registered_before_names_are_exported(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / "environment"
            output_file = Path(directory) / "output"
            observed = []

            def record(value):
                self.assertFalse(env_file.exists())
                observed.append(value)

            with (
                patch.dict(
                    os.environ,
                    {
                        "CI_GIT_REF": REF,
                        "CI_LOCATION": "switzerlandnorth",
                        "GITHUB_ENV": str(env_file),
                        "GITHUB_OUTPUT": str(output_file),
                    },
                ),
                patch.object(scope, "mask", side_effect=record),
            ):
                scope.export_names()
            for line in env_file.read_text().splitlines():
                self.assertIn(line.split("=", 1)[1], observed)
            self.assertFalse(output_file.exists())

    def test_missing_region_fails_without_names_or_decision_outputs(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / "environment"
            output_file = Path(directory) / "output"
            with (
                patch.dict(
                    os.environ,
                    {
                        "CI_GIT_REF": REF,
                        "CI_DEPLOYMENTS_ENABLED": "true",
                        "GITHUB_ENV": str(env_file),
                        "GITHUB_OUTPUT": str(output_file),
                    },
                ),
                patch.object(scope, "mask"),
                self.assertRaises(ValueError),
            ):
                scope.prepare("deployment")
            self.assertFalse(env_file.exists())
            self.assertFalse(output_file.exists())

    def test_inventory_masks_owned_names_and_descendants(self):
        descendant = {"name": "rg-cgi12345678-ws-synthetic", "tags": {}}
        foreign = {"name": "rg-cgi87654321", "tags": {**self.tags, "ci_repository_id": "2"}}
        log = StringIO()
        with patch.object(scope, "azure", return_value=[*self.groups, descendant, foreign]), redirect_stdout(log):
            scope.mask_inventory()
        for value in ("cgi12345678", "12345678", descendant["name"]):
            self.assertIn("::add-mask::" + value + "\n", log.getvalue())
        self.assertNotIn("87654321", log.getvalue())

    def test_inventory_rejects_invalid_shapes(self):
        for groups in ({}, [None], [{"name": 1}]):
            with patch.object(scope, "azure", return_value=groups), self.assertRaises(ValueError):
                scope.mask_inventory()

    def test_legacy_resolver_and_mask_inventory_do_nothing(self):
        with patch.dict(os.environ, {"CI_NAMESPACE": ""}), patch.object(scope, "azure") as azure:
            scope.export_names()
            scope.mask_inventory()
        azure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
