#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CONFIG_NAME = "config.ini"
DEFAULT_PROVIDER = "gitcode"
PLACEHOLDER_TOKENS = {"", "<token>", "your_token_here"}


@dataclass(frozen=True)
class ProviderMeta:
    name: str
    display_name: str
    section: str
    base_url_key: str
    default_url: str
    token_help_text: str
    token_help_url: Optional[str] = None


PROVIDER_META = {
    "gitee": ProviderMeta(
        name="gitee",
        display_name="Gitee",
        section="gitee",
        base_url_key="gitee-url",
        default_url="https://gitee.com",
        token_help_text="Create or update a Gitee personal access token.",
        token_help_url="https://gitee.com/profile/personal_access_tokens/new",
    ),
    "gitcode": ProviderMeta(
        name="gitcode",
        display_name="GitCode",
        section="gitcode",
        base_url_key="gitcode-url",
        default_url="https://gitcode.com",
        token_help_text="Open your GitCode account settings and create or update a personal access token there.",
    ),
}
SUPPORTED_PROVIDERS = tuple(PROVIDER_META)


def _config_dir() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "gitee_util"
    return Path.home() / ".config" / "gitee_util"


def _config_path() -> Path:
    return _config_dir() / CONFIG_NAME


def _members_path(raw_value: str) -> str:
    path = Path(raw_value)
    if path.is_absolute():
        return str(path)
    return str((_config_dir() / path).resolve())


def _is_placeholder_token(token: str) -> bool:
    return token.strip() in PLACEHOLDER_TOKENS


def _ensure_default_layout(config: ConfigParser) -> bool:
    changed = False

    if not config.has_section("general"):
        config.add_section("general")
        changed = True

    current_provider = config.get("general", "provider", fallback="").strip().lower()
    if current_provider not in SUPPORTED_PROVIDERS:
        config.set("general", "provider", DEFAULT_PROVIDER)
        changed = True

    for meta in PROVIDER_META.values():
        if not config.has_section(meta.section):
            config.add_section(meta.section)
            changed = True
        if not config.get(meta.section, meta.base_url_key, fallback="").strip():
            config.set(meta.section, meta.base_url_key, meta.default_url)
            changed = True
        if not config.has_option(meta.section, "token"):
            config.set(meta.section, "token", "")
            changed = True
        if not config.has_option(meta.section, "members"):
            config.set(meta.section, "members", "members.txt")
            changed = True

    return changed


def _load_config(config_path: Path) -> ConfigParser:
    config = ConfigParser()
    if config_path.exists():
        config.read(config_path, encoding="utf-8")
    return config


def _save_config(config: ConfigParser, config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        config.write(handle)


def _print_token_help(meta: ProviderMeta, config_path: Path, *, refresh: bool) -> None:
    action = "refresh" if refresh else "create"
    print(f"{meta.display_name} token setup is required.")
    print(meta.token_help_text)
    if meta.token_help_url:
        print(f"Open this page to {action} the token:")
        print(f"  {meta.token_help_url}")
    print("The token will be stored in:")
    print(f"  {config_path}")


def _prompt_token(meta: ProviderMeta, config_path: Path, *, refresh: bool) -> str:
    if not sys.stdin.isatty():
        _print_token_help(meta, config_path, refresh=refresh)
        raise SystemExit("Interactive token bootstrap requires a TTY.")

    _print_token_help(meta, config_path, refresh=refresh)
    token = input(
        f"Paste {meta.display_name} personal access token"
        " (leave blank to abort) > "
    ).strip()
    if _is_placeholder_token(token):
        raise SystemExit(f"{meta.display_name} token was not provided.")
    return token


def ensure_provider_config(provider: str) -> tuple[str, str, str, str]:
    meta = PROVIDER_META[provider]
    config_path = _config_path()
    config = _load_config(config_path)
    config_exists = config_path.exists()
    changed = _ensure_default_layout(config)

    base_url = config.get(meta.section, meta.base_url_key, fallback=meta.default_url).strip() or meta.default_url
    token = config.get(meta.section, "token", fallback="").strip()
    members = config.get(meta.section, "members", fallback="members.txt").strip() or "members.txt"

    if not config_exists or _is_placeholder_token(token):
        if not config_exists:
            print(f"Config file not found: {config_path}")
        else:
            print(f"{meta.display_name} token is missing in: {config_path}")
        token = _prompt_token(meta, config_path, refresh=False)
        config.set(meta.section, meta.base_url_key, base_url)
        config.set(meta.section, "token", token)
        config.set(meta.section, "members", members)
        _save_config(config, config_path)
        print(f"Saved {meta.display_name} token to {config_path}")
    elif changed:
        _save_config(config, config_path)

    return base_url, token, _members_path(members), str(config_path)


def maybe_refresh_provider_token(provider: str, config_path_value: str) -> Optional[str]:
    meta = PROVIDER_META[provider]
    config_path = Path(config_path_value)
    config = _load_config(config_path)
    changed = _ensure_default_layout(config)
    if changed:
        _save_config(config, config_path)

    if not sys.stdin.isatty():
        _print_token_help(meta, config_path, refresh=True)
        print("Non-interactive mode: update the token in config.ini and rerun the command.")
        return None

    print(f"{meta.display_name} access token may be missing, invalid, or expired.")
    _print_token_help(meta, config_path, refresh=True)
    token = input(
        f"Paste a new {meta.display_name} token now"
        " (leave blank to keep current and abort) > "
    ).strip()
    if _is_placeholder_token(token):
        print(f"{meta.display_name} token was not updated.")
        return None

    config.set(meta.section, "token", token)
    _save_config(config, config_path)
    print(f"Updated {meta.display_name} token in {config_path}")
    return token
