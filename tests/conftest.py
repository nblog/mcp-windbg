"""pytest共享fixture

多数测试不依赖真实的CDB或转储文件；需要真实调试器的测试统一标记为
``requires_cdb``，在缺少环境时跳过。
"""

import glob
import os
import platform

import pytest

from mcp_server_windbg.cdb_session import DEFAULT_CDB_PATHS


#: 可能影响配置解析结果的环境变量
CONFIG_ENV_VARS = (
    "CDB_PATH",
    "SYMBOL_PATH",
    "SOURCE_PATH",
    "DEFAULT_TIMEOUT",
    "MCP_WINDBG_CDB_PATH",
    "MCP_WINDBG_SYMBOL_PATH",
    "MCP_WINDBG_SOURCE_PATH",
    "MCP_WINDBG_TIMEOUT",
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """清除干扰配置解析的环境变量，并在无.env的目录中运行"""
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


@pytest.fixture(scope="session")
def cdb_path():
    """定位真实的cdb.exe，缺失时跳过"""
    if platform.system() != "Windows":
        pytest.skip("CDB仅在Windows上可用")
    for path in DEFAULT_CDB_PATHS:
        if os.path.isfile(path):
            return path
    pytest.skip("未找到cdb.exe")


@pytest.fixture(scope="session")
def real_dump():
    """定位本机上任意一个崩溃转储，缺失时跳过"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        pytest.skip("未设置LOCALAPPDATA")
    dumps = sorted(
        glob.glob(os.path.join(local_app_data, "CrashDumps", "*.*dmp")),
        key=os.path.getsize,
    )
    if not dumps:
        pytest.skip("%LOCALAPPDATA%\\CrashDumps下没有转储文件")
    return dumps[0]
