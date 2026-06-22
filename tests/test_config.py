import pytest

from garmin_sync.config import DEFAULT_STATE_DIR, ConfigError, load_config


def test_load_config_reports_missing_config_file() -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config("missing.yml")


def test_load_config_builds_separate_accounts(tmp_path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
accounts:
  global:
    email: global@example.com
    password: global-password
  cn:
    email: cn@example.com
    password: cn-password
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.global_account.email == "global@example.com"
    assert config.global_account.is_cn is False
    assert config.cn_account.email == "cn@example.com"
    assert config.cn_account.is_cn is True
    assert config.profile == "default"
    assert config.global_account.tokenstore == (
        DEFAULT_STATE_DIR / "profiles/default/tokens/global"
    )
    assert config.cn_account.tokenstore == DEFAULT_STATE_DIR / "profiles/default/tokens/cn"
    assert config.state_dir == DEFAULT_STATE_DIR / "profiles/default"


def test_load_config_reports_missing_required_values(tmp_path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
accounts:
  global:
    email: global@example.com
    password: global-password
  cn:
    email: cn@example.com
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="accounts.cn.password"):
        load_config(config_path)


def test_load_config_selects_named_profile(tmp_path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
profile: backup
profiles:
  default:
    accounts:
      global:
        email: global@example.com
        password: global-password
      cn:
        email: cn@example.com
        password: cn-password
  backup:
    accounts:
      global:
        email: backup-global@example.com
        password: backup-global-password
      cn:
        email: backup-cn@example.com
        password: backup-cn-password
""",
        encoding="utf-8",
    )

    config = load_config(config_path)
    overridden = load_config(config_path, profile="default")

    assert config.profile == "backup"
    assert config.global_account.email == "backup-global@example.com"
    assert config.state_dir == DEFAULT_STATE_DIR / "profiles/backup"
    assert overridden.profile == "default"
    assert overridden.global_account.email == "global@example.com"
