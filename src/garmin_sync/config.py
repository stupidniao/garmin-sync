"""Configuration loading for Garmin sync commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_STATE_DIR = Path("~/.garminsync").expanduser()


class ConfigError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class AccountConfig:
    """Runtime configuration for one Garmin account."""

    email: str = field(repr=False)
    password: str = field(repr=False)
    tokenstore: Path
    is_cn: bool


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration for a compare run."""

    global_account: AccountConfig
    cn_account: AccountConfig
    state_dir: Path


def _required_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"Config key '{name}' must be a mapping")
    return value


def _required_string(mapping: dict[str, Any], key: str, path: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Missing required config value: {path}.{key}")
    return value.strip()


def _account_config(
    accounts: dict[str, Any],
    account_key: str,
    *,
    is_cn: bool,
    tokenstore: Path,
) -> AccountConfig:
    account = _required_mapping(accounts.get(account_key), f"accounts.{account_key}")
    return AccountConfig(
        email=_required_string(account, "email", f"accounts.{account_key}"),
        password=_required_string(account, "password", f"accounts.{account_key}"),
        tokenstore=tokenstore,
        is_cn=is_cn,
    )


def load_config(config_path: Path | str = Path("config.yml")) -> AppConfig:
    """Load runtime configuration from a YAML config file."""

    path = Path(config_path).expanduser()
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}. Copy config.example.yml to config.yml."
        )

    with path.open("r", encoding="utf-8") as handle:
        raw_config = yaml.safe_load(handle)

    config = _required_mapping(raw_config, "root")
    accounts = _required_mapping(config.get("accounts"), "accounts")

    return AppConfig(
        global_account=_account_config(
            accounts,
            "global",
            is_cn=False,
            tokenstore=DEFAULT_STATE_DIR / "tokens/global",
        ),
        cn_account=_account_config(
            accounts,
            "cn",
            is_cn=True,
            tokenstore=DEFAULT_STATE_DIR / "tokens/cn",
        ),
        state_dir=DEFAULT_STATE_DIR,
    )
