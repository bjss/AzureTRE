"""Exercise repository boundaries without Azure access."""

from copy import deepcopy
import os
from pathlib import Path
import sys
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


if __name__ == "__main__":
    unittest.main()
