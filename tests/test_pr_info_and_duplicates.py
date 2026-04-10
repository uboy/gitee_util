import io
import importlib
import os
import sys
import tempfile
import types
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


_fake_prompt_toolkit = types.ModuleType("prompt_toolkit")
_fake_prompt_toolkit.prompt = lambda *args, **kwargs: ""

with patch.dict(sys.modules, {"prompt_toolkit": _fake_prompt_toolkit}):
    gitcode_util = importlib.import_module("gitcode_util")
    gitee_util = importlib.import_module("gitee_util")


class _FakeClient:
    def __init__(self):
        self.issue_creates = 0
        self.pr_creates = 0

    def validate_repository(self, owner, repo):
        return True

    def validate_branch_exists(self, owner, repo, branch):
        return True

    def list_issues(self, owner, repo, state="open", per_page=50, max_results=100):
        return [
            {
                "number": 17,
                "title": "Same issue title",
                "html_url": "https://example.invalid/issues/17",
            }
        ]

    def list_pull_requests(self, owner, repo, state="open", author=None, per_page=50, max_results=0):
        return [
            {
                "number": 42,
                "title": "Same PR title",
                "html_url": "https://example.invalid/pr/42",
                "head": {
                    "ref": "feature_branch",
                    "user": {"login": "srcowner"},
                    "repo": {"full_name": "srcowner/srcrepo"},
                },
                "base": {"ref": "master"},
            }
        ]

    def create_issue(self, *args, **kwargs):
        self.issue_creates += 1
        raise AssertionError("create_issue should not be called when duplicate is found")

    def create_pull_request(self, *args, **kwargs):
        self.pr_creates += 1
        raise AssertionError("create_pull_request should not be called when duplicate is found")

    def get_single_pull_request(self, owner, repo, pr_id):
        return {
            "number": pr_id,
            "title": "Improve chip behavior",
            "state": "open",
            "html_url": "https://example.invalid/pr/55",
            "user": {"login": "alice"},
            "created_at": "2026-04-10T11:00:00Z",
            "updated_at": "2026-04-10T12:00:00Z",
            "body": "First paragraph\n\nSecond paragraph",
            "labels": [{"name": "waiting_for_review"}],
            "assignees": [
                {"login": "reviewer1", "assignee": True, "accept": True},
                {"login": "owner1", "code_owner": True, "accept": False},
            ],
            "testers": [{"login": "tester1", "accept": True}],
            "base": {"ref": "master"},
            "head": {"ref": "feature_branch", "repo": {"full_name": "alice/fork_repo"}},
        }

    def get_pull_request_files(self, owner, repo, pr_id):
        return [
            {"filename": "foundation/arkui/ace_engine/foo.cpp", "status": "modified"},
            {"filename": "test/xts/acts/bar.ets", "status": "added"},
        ]

    def get_file_from_repo(self, owner, repo, path, ref="master"):
        if path.endswith("owner_config.json"):
            return """{
  "groups": {
    "owner1": ["member_a", "member_b"],
    "reviewer1": ["reviewer_member"]
  }
}"""
        if path.endswith("CODEOWNERS"):
            return """
foundation/arkui/ace_engine/foo.cpp @owner1
test/xts/acts/bar.ets @reviewer1 @owner1
""".strip()
        return None


class PrInfoAndDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.desc_file = Path(self.tempdir.name) / "desc.md"
        self.desc_file.write_text("description body", encoding="utf-8")

    def test_show_pr_renders_details_for_both_providers(self):
        args = Namespace(url=None, repo="owner/repo", pr_id="55")
        for module in (gitcode_util, gitee_util):
            with self.subTest(module=module.__name__):
                client = _FakeClient()
                output = io.StringIO()
                with redirect_stdout(output):
                    module.handle_show_pr(args, client)
                rendered = output.getvalue()
                self.assertIn("PR #55 in owner/repo", rendered)
                self.assertIn("Improve chip behavior", rendered)
                self.assertIn("Reviewers: reviewer1 (accepted)", rendered)
                self.assertIn("Code Owners: owner1 (pending)", rendered)
                self.assertIn("Code Owner Group Members:", rendered)
                self.assertIn("- owner1: member_a, member_b", rendered)
                self.assertIn("CODEOWNERS Matches:", rendered)
                self.assertIn("foundation/arkui/ace_engine/foo.cpp", rendered)
                self.assertIn("owners: owner1", rendered)
                self.assertIn("members: owner1: member_a, member_b", rendered)
                self.assertIn("Changed Files (2):", rendered)
                self.assertIn("foundation/arkui/ace_engine/foo.cpp [modified]", rendered)

    def test_create_issue_blocks_duplicate_for_both_providers(self):
        args = Namespace(
            repo="owner/repo",
            type="bug",
            title="Same issue title",
            desc_file=str(self.desc_file),
            allow_duplicate=False,
            yes=False,
        )
        for module in (gitcode_util, gitee_util):
            with self.subTest(module=module.__name__):
                client = _FakeClient()
                output = io.StringIO()
                with redirect_stdout(output):
                    module.handle_create_issue(args, client)
                rendered = output.getvalue()
                self.assertIn("looks like a duplicate", rendered)
                self.assertIn("https://example.invalid/issues/17", rendered)
                self.assertEqual(client.issue_creates, 0)

    def test_create_pr_blocks_duplicate_for_both_providers(self):
        args = Namespace(
            repo="target/repo",
            base="master",
            desc_file=str(self.desc_file),
            allow_duplicate=False,
            yes=False,
        )
        for module in (gitcode_util, gitee_util):
            with self.subTest(module=module.__name__):
                client = _FakeClient()
                output = io.StringIO()
                with patch.object(module, "detect_git_repo", return_value=("srcowner", "srcrepo", "feature_branch")):
                    with patch.object(module, "prompt", side_effect=["Same PR title"]):
                        with redirect_stdout(output):
                            module.handle_create_pr(args, client)
                rendered = output.getvalue()
                self.assertIn("looks like a duplicate", rendered)
                self.assertIn("https://example.invalid/pr/42", rendered)
                self.assertEqual(client.pr_creates, 0)

    def test_show_pr_main_uses_runtime_config_without_token_bootstrap(self):
        class _ReadOnlyClient:
            def __init__(self, base_url, token, members, config_path):
                self.base_url = base_url
                self.token = token
                self.members = members
                self.config_path = config_path

            def get_single_pull_request(self, owner, repo, pr_id):
                return _FakeClient().get_single_pull_request(owner, repo, pr_id)

            def get_pull_request_files(self, owner, repo, pr_id):
                return _FakeClient().get_pull_request_files(owner, repo, pr_id)

            def get_file_from_repo(self, owner, repo, path, ref="master"):
                return _FakeClient().get_file_from_repo(owner, repo, path, ref=ref)

        for module in (gitcode_util, gitee_util):
            with self.subTest(module=module.__name__):
                output = io.StringIO()
                with patch.object(module, "load_config", side_effect=AssertionError("token bootstrap must not run")):
                    with patch.object(module, "load_runtime_config", return_value=("https://example.invalid", "", "/tmp/members.txt", "/tmp/config.ini")):
                        with patch.object(module, "GiteeClient", _ReadOnlyClient):
                            with patch.object(sys, "argv", ["tool", "show-pr", "--url", "https://gitcode.com/owner/repo/pull/55"]):
                                with redirect_stdout(output):
                                    rc = module.main()
                rendered = output.getvalue()
                self.assertIn("PR #55 in owner/repo", rendered)
                self.assertIn("Improve chip behavior", rendered)
                self.assertIn("Code Owner Group Members:", rendered)
                self.assertNotIn("token setup is required", rendered.lower())
                self.assertNotEqual(rc, 1)

    def test_gitcode_client_uses_private_token_header_when_token_exists(self):
        client = gitcode_util.GiteeClient(
            "https://gitcode.com",
            "secret-token",
            "/tmp/members.txt",
            "/tmp/config.ini",
        )

        self.assertTrue(client.has_token)
        self.assertEqual(client.session.headers.get("private-token"), "secret-token")
        self.assertNotIn("access_token", client.session.params)


if __name__ == "__main__":
    unittest.main()
