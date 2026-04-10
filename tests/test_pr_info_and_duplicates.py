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


if __name__ == "__main__":
    unittest.main()
