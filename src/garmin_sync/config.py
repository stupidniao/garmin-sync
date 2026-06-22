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

    profile: str
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
    path_prefix: str = "accounts",
) -> AccountConfig:
    account_path = f"{path_prefix}.{account_key}"
    account = _required_mapping(accounts.get(account_key), account_path)
    return AccountConfig(
        email=_required_string(account, "email", account_path),
        password=_required_string(account, "password", account_path),
        tokenstore=tokenstore,
        is_cn=is_cn,
    )


def load_config(
    config_path: Path | str = Path("config.yml"),
    *,
    profile: str | None = None,
) -> AppConfig:
    """Load runtime configuration from a YAML config file."""

    path = Path(config_path).expanduser()
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}. Copy config.example.yml to config.yml."
        )

    with path.open("r", encoding="utf-8") as handle:
        raw_config = yaml.safe_load(handle)

    config = _required_mapping(raw_config, "root")
    profile_name, accounts, account_path_prefix = _profile_accounts(config, profile)
    profile_state_dir = DEFAULT_STATE_DIR / "profiles" / profile_name

    return AppConfig(
        profile=profile_name,
        global_account=_account_config(
            accounts,
            "global",
            is_cn=False,
            tokenstore=profile_state_dir / "tokens/global",
            path_prefix=account_path_prefix,
        ),
        cn_account=_account_config(
            accounts,
            "cn",
            is_cn=True,
            tokenstore=profile_state_dir / "tokens/cn",
            path_prefix=account_path_prefix,
        ),
        state_dir=profile_state_dir,
    )


def _profile_accounts(
    config: dict[str, Any],
    requested_profile: str | None,
) -> tuple[str, dict[str, Any], str]:
    profiles = config.get("profiles")
    configured_profile = config.get("profile")
    if configured_profile is not None and not isinstance(configured_profile, str):
        raise ConfigError("Config key 'profile' must be a string")

    profile_name = (requested_profile or configured_profile or "default").strip()
    if not profile_name:
        raise ConfigError("Profile name must not be empty")

    if profiles is not None:
        profile_map = _required_mapping(profiles, "profiles")
        selected = _required_mapping(
            profile_map.get(profile_name),
            f"profiles.{profile_name}",
        )
        return (
            profile_name,
            _required_mapping(
                selected.get("accounts"),
                f"profiles.{profile_name}.accounts",
            ),
            f"profiles.{profile_name}.accounts",
        )

    if profile_name != "default":
        raise ConfigError(
            f"Profile '{profile_name}' not found. Legacy config only defines profile 'default'."
        )
    return profile_name, _required_mapping(config.get("accounts"), "accounts"), "accounts"
