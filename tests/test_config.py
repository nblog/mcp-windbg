"""WinDbgPluginConfig的配置解析测试

覆盖回归点：``symbol_path`` / ``source_path`` 不得绑定环境变量，因为CDB自身
已经识别 ``_NT_SYMBOL_PATH`` 与 ``_NT_SOURCE_PATH``。
"""

import warnings

import pytest

from mcp_server_windbg.windbg_plugin import (
    DEFAULT_SYMBOL_PATH,
    WinDbgPlugin,
    WinDbgPluginConfig,
)


def test_symbol_path_defaults_to_microsoft_symbol_server(clean_env):
    assert WinDbgPluginConfig().symbol_path == DEFAULT_SYMBOL_PATH


def test_defaults(clean_env):
    config = WinDbgPluginConfig()
    assert config.cdb_path is None
    assert config.source_path is None
    assert config.timeout == 600


def test_symbol_path_ignores_bare_env_var(clean_env):
    """CDB通过_NT_SYMBOL_PATH处理符号路径，插件不应再占用裸名SYMBOL_PATH"""
    clean_env.setenv("SYMBOL_PATH", "C:\\should-be-ignored")
    assert WinDbgPluginConfig().symbol_path == DEFAULT_SYMBOL_PATH


def test_source_path_ignores_bare_env_var(clean_env):
    """CDB通过_NT_SOURCE_PATH处理源路径，插件不应再占用裸名SOURCE_PATH"""
    clean_env.setenv("SOURCE_PATH", "C:\\should-be-ignored")
    assert WinDbgPluginConfig().source_path is None


@pytest.mark.parametrize(
    "var,attr",
    [("MCP_WINDBG_SYMBOL_PATH", "symbol_path"), ("MCP_WINDBG_SOURCE_PATH", "source_path")],
)
def test_namespaced_env_vars_still_apply(clean_env, var, attr):
    """带前缀的变量不会与调试器原生变量混淆，因此仍然可用"""
    clean_env.setenv(var, "C:\\explicit")
    assert getattr(WinDbgPluginConfig(), attr) == "C:\\explicit"


def test_cdb_path_reads_env(clean_env):
    clean_env.setenv("CDB_PATH", "C:\\tools\\cdb.exe")
    assert WinDbgPluginConfig().cdb_path == "C:\\tools\\cdb.exe"


def test_timeout_reads_env(clean_env):
    clean_env.setenv("DEFAULT_TIMEOUT", "42")
    assert WinDbgPluginConfig().timeout == 42


def test_explicit_arguments_win_over_env(clean_env):
    clean_env.setenv("CDB_PATH", "C:\\from-env\\cdb.exe")
    config = WinDbgPluginConfig(cdb_path="C:\\explicit\\cdb.exe", symbol_path="")
    assert config.cdb_path == "C:\\explicit\\cdb.exe"
    assert config.symbol_path == ""


def test_construction_emits_no_deprecation_warning(clean_env):
    """Field(env=...) 在Pydantic v2中已弃用，配置类不得再触发该告警"""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        WinDbgPluginConfig()


def test_plugin_adopts_config_values(clean_env):
    config = WinDbgPluginConfig(
        cdb_path="C:\\cdb.exe",
        symbol_path="C:\\sym",
        source_path="C:\\src",
        timeout=7,
    )
    plugin = WinDbgPlugin(config)
    assert (plugin.cdb_path, plugin.symbol_path, plugin.source_path, plugin.timeout) == (
        "C:\\cdb.exe",
        "C:\\sym",
        "C:\\src",
        7,
    )


def test_plugin_without_config_uses_defaults(clean_env):
    """未传配置时也应享受默认符号服务器，而非退化为None"""
    assert WinDbgPlugin().symbol_path == DEFAULT_SYMBOL_PATH
