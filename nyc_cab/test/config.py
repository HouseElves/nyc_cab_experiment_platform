"""Tests for :mod:`nyc_cab.config`.

These tests exercise the branches in :func:`nyc_cab.config.load_config` and
its validation helpers, including:

* local-environment defaults,
* required-in-non-local behavior for ``NYC_CAB_DATA_ROOT``,
* case and whitespace handling across supported variables,
* explicit rejection of malformed environment and log-level values,
* the immutability contract on :class:`RuntimeConfig` and its sub-dataclasses,
* derivation of medallion layer paths from ``data_root``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nyc_cab import constants
from nyc_cab.config import Environment, PathsConfig, RuntimeConfig, load_config
from nyc_cab.exceptions import InvalidConfigError, MissingConfigError


def _env(**overrides: str) -> dict[str, str]:
    """Return an environment mapping containing only the supplied overrides."""
    return dict(overrides)


def test_empty_environ_yields_local_runtime_config() -> None:
    """An empty mapping loads a fully defaulted local runtime configuration."""
    runtime = load_config(_env())
    assert runtime.environment is Environment.LOCAL
    assert runtime.log_level == constants.DEFAULT_LOG_LEVEL


def test_local_environment_uses_default_data_root() -> None:
    """Omitting ``NYC_CAB_DATA_ROOT`` in local resolves the default."""
    runtime = load_config(_env())
    expected = Path(constants.DEFAULT_DATA_ROOT_LOCAL).expanduser().resolve()
    assert runtime.paths.data_root == expected


def test_local_environment_accepts_explicit_data_root(tmp_path: Path) -> None:
    """Providing ``NYC_CAB_DATA_ROOT`` overrides the local default."""
    runtime = load_config(_env(NYC_CAB_DATA_ROOT=str(tmp_path)))
    assert runtime.paths.data_root == tmp_path.resolve()


def test_local_environment_treats_whitespace_data_root_as_absent() -> None:
    """Whitespace-only ``NYC_CAB_DATA_ROOT`` falls back to the local default."""
    runtime = load_config(_env(NYC_CAB_DATA_ROOT="   "))
    expected = Path(constants.DEFAULT_DATA_ROOT_LOCAL).expanduser().resolve()
    assert runtime.paths.data_root == expected


def test_environment_value_is_case_insensitive(tmp_path: Path) -> None:
    """Uppercase environment values resolve to the correct enum member."""
    runtime = load_config(
        _env(NYC_CAB_ENVIRONMENT="DEV", NYC_CAB_DATA_ROOT=str(tmp_path))
    )
    assert runtime.environment is Environment.DEV


def test_environment_whitespace_is_stripped(tmp_path: Path) -> None:
    """Leading and trailing whitespace around the environment value is ignored."""
    runtime = load_config(
        _env(NYC_CAB_ENVIRONMENT="  prod  ", NYC_CAB_DATA_ROOT=str(tmp_path))
    )
    assert runtime.environment is Environment.PROD


def test_invalid_environment_value_raises_invalid_config_error() -> None:
    """An unsupported environment value raises ``InvalidConfigError``."""
    with pytest.raises(InvalidConfigError) as error_info:
        load_config(_env(NYC_CAB_ENVIRONMENT="staging"))

    assert error_info.value.variable == constants.ENV_VAR_ENVIRONMENT
    assert error_info.value.value == "staging"


def test_dev_environment_requires_data_root() -> None:
    """``NYC_CAB_DATA_ROOT`` is required when environment is ``dev``."""
    with pytest.raises(MissingConfigError) as error_info:
        load_config(_env(NYC_CAB_ENVIRONMENT="dev"))

    assert error_info.value.variable == constants.ENV_VAR_DATA_ROOT


def test_prod_environment_requires_data_root() -> None:
    """``NYC_CAB_DATA_ROOT`` is required when environment is ``prod``."""
    with pytest.raises(MissingConfigError):
        load_config(_env(NYC_CAB_ENVIRONMENT="prod"))


def test_non_local_environment_rejects_whitespace_data_root() -> None:
    """A whitespace-only data root still counts as missing outside local."""
    with pytest.raises(MissingConfigError):
        load_config(_env(NYC_CAB_ENVIRONMENT="prod", NYC_CAB_DATA_ROOT="   "))


def test_data_root_is_resolved_to_absolute_path(tmp_path: Path) -> None:
    """Configured data roots are expanded and resolved to absolute paths."""
    runtime = load_config(_env(NYC_CAB_DATA_ROOT=str(tmp_path)))
    assert runtime.paths.data_root.is_absolute()


def test_medallion_layer_paths_derive_from_data_root(tmp_path: Path) -> None:
    """Bronze, Silver, and Gold paths sit directly beneath ``data_root``."""
    runtime = load_config(_env(NYC_CAB_DATA_ROOT=str(tmp_path)))
    data_root = runtime.paths.data_root

    assert runtime.paths.bronze == data_root / constants.BRONZE_LAYER
    assert runtime.paths.silver == data_root / constants.SILVER_LAYER
    assert runtime.paths.gold == data_root / constants.GOLD_LAYER


def test_log_level_is_normalized_to_uppercase() -> None:
    """Lowercase log levels are accepted and normalized to uppercase."""
    runtime = load_config(_env(NYC_CAB_LOG_LEVEL="debug"))
    assert runtime.log_level == "DEBUG"


def test_log_level_whitespace_is_stripped() -> None:
    """Leading and trailing whitespace around log level is ignored."""
    runtime = load_config(_env(NYC_CAB_LOG_LEVEL="  warning  "))
    assert runtime.log_level == "WARNING"


def test_invalid_log_level_raises_invalid_config_error() -> None:
    """An unsupported log level raises ``InvalidConfigError``."""
    with pytest.raises(InvalidConfigError) as error_info:
        load_config(_env(NYC_CAB_LOG_LEVEL="verbose"))

    assert error_info.value.variable == constants.ENV_VAR_LOG_LEVEL
    assert error_info.value.value == "verbose"


def test_runtime_config_is_frozen(tmp_path: Path) -> None:
    """:class:`RuntimeConfig` rejects attribute mutation after construction."""
    runtime = load_config(_env(NYC_CAB_DATA_ROOT=str(tmp_path)))
    with pytest.raises(dataclasses.FrozenInstanceError):
        runtime.environment = Environment.PROD  # type: ignore[misc]


def test_paths_config_is_frozen(tmp_path: Path) -> None:
    """:class:`PathsConfig` rejects attribute mutation after construction."""
    runtime = load_config(_env(NYC_CAB_DATA_ROOT=str(tmp_path)))
    with pytest.raises(dataclasses.FrozenInstanceError):
        runtime.paths.data_root = tmp_path  # type: ignore[misc]


def test_load_config_falls_back_to_process_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When called with no argument, ``load_config`` reads ``os.environ``."""
    for name in (
        constants.ENV_VAR_ENVIRONMENT,
        constants.ENV_VAR_DATA_ROOT,
        constants.ENV_VAR_LOG_LEVEL,
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv(constants.ENV_VAR_ENVIRONMENT, "dev")
    monkeypatch.setenv(constants.ENV_VAR_DATA_ROOT, str(tmp_path))
    monkeypatch.setenv(constants.ENV_VAR_LOG_LEVEL, "error")

    runtime = load_config()

    assert runtime.environment is Environment.DEV
    assert runtime.paths.data_root == tmp_path.resolve()
    assert runtime.log_level == "ERROR"


def test_runtime_config_constructor_requires_named_components(tmp_path: Path) -> None:
    """Direct construction yields a valid runtime configuration object."""
    paths = PathsConfig(data_root=tmp_path.resolve())
    runtime = RuntimeConfig(
        environment=Environment.LOCAL,
        paths=paths,
        log_level="INFO",
    )
    assert runtime.paths is paths


def test_environment_enum_values_are_lowercase_tokens() -> None:
    """Each environment enum value matches the caller-facing lowercase token."""
    assert {member.value for member in Environment} == {"local", "dev", "prod"}
