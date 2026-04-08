#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import importlib
import sys
from configparser import ConfigParser
from config_bootstrap import DEFAULT_PROVIDER, SUPPORTED_PROVIDERS as BOOTSTRAP_PROVIDERS, _config_path

SUPPORTED_PROVIDERS = set(BOOTSTRAP_PROVIDERS)


def _load_provider_from_config() -> str:
    config_path = _config_path()
    config = ConfigParser()
    config.read(config_path, encoding="utf-8")
    provider = config.get("general", "provider", fallback=DEFAULT_PROVIDER).strip().lower()
    if provider in SUPPORTED_PROVIDERS:
        return provider
    return DEFAULT_PROVIDER


def _extract_provider(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", choices=sorted(SUPPORTED_PROVIDERS))
    args, remaining = parser.parse_known_args(argv)
    return args.provider, remaining


def main():
    provider_arg, passthrough_argv = _extract_provider(sys.argv[1:])
    provider = provider_arg or _load_provider_from_config()
    module_name = "gitee_util" if provider == "gitee" else "gitcode_util"
    module = importlib.import_module(module_name)
    sys.argv = [sys.argv[0], *passthrough_argv]
    module.main()


if __name__ == "__main__":
    main()
