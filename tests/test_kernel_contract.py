"""Semantic Kernel契约测试

SK 1.44.1 的 ``_parse_parameter`` 无法处理 ``typing.Optional[X]``：注册时
``type_object`` 会保留Optional包装，调用时对其求值抛出异常，导致所有带可选参数
的工具在真实MCP调用中失败。PEP 604 的 ``X | None`` 会被归一为具体类型。
这些测试锁定该约束，避免回退。
"""

import pytest
from semantic_kernel import Kernel

from mcp_server_windbg.windbg_plugin import WinDbgPlugin

EXPECTED_TOOLS = {
    "open_windbg_dump",
    "open_windbg_remote",
    "run_windbg_cmd",
    "close_windbg_dump",
    "close_windbg_remote",
    "list_windbg_dumps",
    "list_windbg_sessions",
}


@pytest.fixture
def plugin_functions(clean_env):
    kernel = Kernel()
    kernel.add_plugin(WinDbgPlugin(), plugin_name="windbg")
    return kernel.get_plugin("windbg").functions


def test_all_tools_registered(plugin_functions):
    assert set(plugin_functions) == EXPECTED_TOOLS


def test_no_parameter_keeps_an_optional_wrapper(plugin_functions):
    """任何残留的Optional[...]都会让SK在调用期抛出解析异常"""
    unparseable = [
        f"{name}.{param.name}={param.type_object!r}"
        for name, function in plugin_functions.items()
        for param in function.parameters
        if "Optional" in repr(param.type_object)
    ]
    assert unparseable == []


def test_optional_parameters_resolve_to_concrete_types(plugin_functions):
    dump_params = {p.name: p for p in plugin_functions["open_windbg_dump"].parameters}
    assert dump_params["dump_path"].type_object is str
    assert dump_params["dump_path"].is_required is False

    cmd_params = {p.name: p for p in plugin_functions["run_windbg_cmd"].parameters}
    assert cmd_params["timeout"].type_object is int
    assert cmd_params["dump_path"].type_object is str


def test_dump_path_is_optional_so_discovery_branch_is_reachable(plugin_functions):
    """dump_path曾是必填参数，使转储发现分支成为死代码"""
    dump_path = next(
        p for p in plugin_functions["open_windbg_dump"].parameters if p.name == "dump_path"
    )
    assert dump_path.is_required is False


def test_required_parameters_stay_required(plugin_functions):
    command = next(
        p for p in plugin_functions["run_windbg_cmd"].parameters if p.name == "command"
    )
    connection = next(
        p for p in plugin_functions["open_windbg_remote"].parameters
        if p.name == "connection_string"
    )
    assert command.is_required is True
    assert connection.is_required is True


def test_connection_string_has_no_field_sentinel_default(plugin_functions):
    """该参数曾错误地把 Field(...) 当作函数默认值，使哨兵对象泄漏进签名"""
    connection = next(
        p for p in plugin_functions["open_windbg_remote"].parameters
        if p.name == "connection_string"
    )
    assert "FieldInfo" not in repr(connection.default_value)


def test_descriptions_are_ascii(plugin_functions):
    """对外暴露的描述保持英文，避免非UTF-8控制台代码页下乱码"""
    non_ascii = []
    for name, function in plugin_functions.items():
        if not function.description.isascii():
            non_ascii.append(name)
        for param in function.parameters:
            if param.description and not param.description.isascii():
                non_ascii.append(f"{name}.{param.name}")
    assert non_ascii == []


def test_mcp_server_builds(clean_env):
    kernel = Kernel()
    kernel.add_plugin(WinDbgPlugin(), plugin_name="windbg")
    server = kernel.as_mcp_server(server_name="test", version="0.0.0")
    assert server is not None
