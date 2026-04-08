import io
import importlib
import sys
import types
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest.mock import patch


_fake_prompt_toolkit = types.ModuleType("prompt_toolkit")
_fake_prompt_toolkit.prompt = lambda *args, **kwargs: ""

with patch.dict(sys.modules, {"prompt_toolkit": _fake_prompt_toolkit}):
    gitcode_util = importlib.import_module("gitcode_util")
    gitee_util = importlib.import_module("gitee_util")


class _FakeClient:
    def __init__(self):
        self.members = "/nonexistent-members.txt"
        self.calls = []
        self.prs = []

    def list_pull_requests(self, owner, repo, state, author, per_page=50, max_results=0):
        self.calls.append((owner, repo, state, author))
        return list(self.prs)

    def get_single_pull_request(self, owner, repo, number):
        return {}

    def get_original_repo(self, owner, repo):
        raise AssertionError("get_original_repo should not be used for default list-pr mode")


class ListPrDefaultsTest(unittest.TestCase):
    def _default_args(self):
        return Namespace(
            file=None,
            user=None,
            repos=None,
            state=None,
            all=False,
            include_draft=False,
            since=None,
            group_by_user=False,
            extended_info=False,
            limit=30,
        )

    def test_default_list_pr_uses_default_repo_without_prompt(self):
        expected_repo = "openharmony/arkui_ace_engine"
        cases = [
            (gitcode_util, expected_repo),
            (gitee_util, expected_repo),
        ]

        for module, exp_repo in cases:
            with self.subTest(module=module.__name__):
                client = _FakeClient()
                output = io.StringIO()
                args = self._default_args()

                with patch.object(module, "prompt", side_effect=AssertionError("prompt should not be called")):
                    with patch.object(module, "detect_git_repo", side_effect=AssertionError("detect_git_repo should not be used")):
                        with patch.object(module, "get_owner_config", return_value={}):
                            with redirect_stdout(output):
                                module.handle_list_pr(args, client)

                owner, repo = exp_repo.split("/")
                self.assertEqual(client.calls, [(owner, repo, "open", None)])
                self.assertIn(f"Using default: {exp_repo}", output.getvalue())

    def test_list_pr_shows_base_branch_in_default_output(self):
        cases = [
            (gitcode_util, "mergeable_state"),
            (gitee_util, "mergeable"),
        ]

        for module, conflict_key in cases:
            with self.subTest(module=module.__name__):
                client = _FakeClient()
                client.prs = [
                    {
                        "number": 123,
                        "title": "Test PR",
                        "state": "open",
                        "user": {"login": "dev1"},
                        "created_at": "2026-04-08T10:00:00+00:00",
                        "html_url": "https://example.invalid/pr/123",
                        "base": {"ref": "OpenHarmony_feature_20260408"},
                    }
                ]
                detailed_payload = {conflict_key: {}}
                output = io.StringIO()
                args = self._default_args()

                with patch.object(module, "get_owner_config", return_value={}):
                    with patch.object(client, "get_single_pull_request", return_value=detailed_payload):
                        with redirect_stdout(output):
                            module.handle_list_pr(args, client)

                rendered = output.getvalue()
                self.assertIn("Test PR", rendered)
                self.assertIn("base: OpenHarmony_feature_20260408", rendered)


if __name__ == "__main__":
    unittest.main()
