import os
import unittest
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory

import config_bootstrap
import git_host_util


class ProviderDefaultsTest(unittest.TestCase):
    def setUp(self):
        self._old_xdg_config_home = os.environ.get("XDG_CONFIG_HOME")

    def tearDown(self):
        if self._old_xdg_config_home is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg_config_home

    def test_default_layout_uses_gitcode_provider(self):
        config = ConfigParser()

        changed = config_bootstrap._ensure_default_layout(config)

        self.assertTrue(changed)
        self.assertEqual(config.get("general", "provider"), "gitcode")
        self.assertEqual(config_bootstrap.DEFAULT_PROVIDER, "gitcode")

    def test_default_layout_preserves_existing_provider(self):
        config = ConfigParser()
        config.add_section("general")
        config.set("general", "provider", "gitee")

        config_bootstrap._ensure_default_layout(config)

        self.assertEqual(config.get("general", "provider"), "gitee")

    def test_ensure_provider_config_keeps_existing_provider(self):
        with TemporaryDirectory() as tempdir:
            os.environ["XDG_CONFIG_HOME"] = tempdir
            config_path = Path(tempdir) / "gitee_util" / "config.ini"
            config_path.parent.mkdir(parents=True, exist_ok=True)

            config = ConfigParser()
            config.add_section("general")
            config.set("general", "provider", "gitee")
            config.add_section("gitee")
            config.set("gitee", "gitee-url", "https://gitee.com")
            config.set("gitee", "token", "gitee-token")
            config.set("gitee", "members", "gitee-members.txt")
            config.add_section("gitcode")
            config.set("gitcode", "gitcode-url", "https://gitcode.com")
            config.set("gitcode", "token", "gitcode-token")
            config.set("gitcode", "members", "gitcode-members.txt")
            with config_path.open("w", encoding="utf-8") as handle:
                config.write(handle)

            base_url, token, members, returned_path = config_bootstrap.ensure_provider_config("gitcode")

            saved = ConfigParser()
            saved.read(config_path, encoding="utf-8")
            self.assertEqual(saved.get("general", "provider"), "gitee")
            self.assertEqual(base_url, "https://gitcode.com")
            self.assertEqual(token, "gitcode-token")
            self.assertEqual(members, str((config_path.parent / "gitcode-members.txt").resolve()))
            self.assertEqual(returned_path, str(config_path))

    def test_git_host_util_defaults_to_gitcode_without_config(self):
        with TemporaryDirectory() as tempdir:
            os.environ["XDG_CONFIG_HOME"] = tempdir

            provider = git_host_util._load_provider_from_config()

            self.assertEqual(provider, "gitcode")


if __name__ == "__main__":
    unittest.main()
