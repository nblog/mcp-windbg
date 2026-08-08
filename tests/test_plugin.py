"""WinDbgPlugin工具函数测试"""

import json
import os
import winreg
from unittest.mock import patch

import pytest

from mcp_server_windbg.cdb_session import SessionManager
from mcp_server_windbg.windbg_plugin import WinDbgPlugin


@pytest.fixture
def plugin(clean_env):
    """使用独立SessionManager的插件，避免污染全局会话状态"""
    instance = WinDbgPlugin()
    instance.session_manager = SessionManager(idle_timeout=0)
    return instance


def _call(plugin_method, **kwargs):
    return json.loads(plugin_method(**kwargs))


class TestLocalDumpsPath:
    """注册表转储路径解析
    
    该路径曾在原始字符串中写成 ``r"...\\\\Microsoft\\\\..."``，导致子键名带有
    重复反斜杠、``LocalDumps`` 查询恒定失败。
    """
    
    def test_registry_subkey_has_no_doubled_separators(self, plugin):
        captured = {}
        
        def fake_open_key(hive, subkey):
            captured["subkey"] = subkey
            raise OSError("not present")
        
        with patch.object(winreg, "OpenKey", side_effect=fake_open_key):
            plugin._get_local_dumps_path()
        
        assert "\\\\" not in captured["subkey"]
        assert captured["subkey"] == (
            r"SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps"
        )
    
    def test_falls_back_to_local_appdata(self, plugin, tmp_path):
        crash_dumps = tmp_path / "CrashDumps"
        crash_dumps.mkdir()
        with patch.object(winreg, "OpenKey", side_effect=OSError):
            plugin_env = {"LOCALAPPDATA": str(tmp_path)}
            with patch.dict(os.environ, plugin_env):
                assert plugin._get_local_dumps_path() == str(crash_dumps)
    
    def test_returns_none_when_nothing_available(self, plugin, tmp_path):
        with patch.object(winreg, "OpenKey", side_effect=OSError):
            with patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
                assert plugin._get_local_dumps_path() is None


class TestListDumps:
    def test_lists_dump_files(self, plugin, tmp_path):
        (tmp_path / "a.dmp").write_bytes(b"x")
        (tmp_path / "b.mdmp").write_bytes(b"xx")
        (tmp_path / "notes.txt").write_text("ignored")
        
        result = _call(plugin.list_windbg_dumps, directory_path=str(tmp_path))
        
        assert result["success"] is True
        assert result["count"] == 2
        assert {os.path.basename(d["path"]) for d in result["dump_files"]} == {
            "a.dmp",
            "b.mdmp",
        }
    
    def test_empty_directory(self, plugin, tmp_path):
        result = _call(plugin.list_windbg_dumps, directory_path=str(tmp_path))
        assert result["success"] is True
        assert result["dump_files"] == []
    
    def test_missing_directory(self, plugin, tmp_path):
        result = _call(
            plugin.list_windbg_dumps, directory_path=str(tmp_path / "absent")
        )
        assert result["success"] is False
        assert "error" in result


class TestOpenDumpDiscovery:
    """省略dump_path时的转储发现分支
    
    dump_path曾是必填参数，使该分支不可达。
    """
    
    def test_no_argument_returns_available_dumps(self, plugin, tmp_path):
        (tmp_path / "crash.dmp").write_bytes(b"x" * 2048)
        with patch.object(plugin, "_get_local_dumps_path", return_value=str(tmp_path)):
            result = _call(plugin.open_windbg_dump)
        
        assert result["success"] is False
        assert result["local_dumps_path"] == str(tmp_path)
        assert [os.path.basename(d["path"]) for d in result["available_dumps"]] == [
            "crash.dmp"
        ]
    
    def test_no_argument_without_dumps_path(self, plugin):
        with patch.object(plugin, "_get_local_dumps_path", return_value=None):
            result = _call(plugin.open_windbg_dump)
        assert result["success"] is False
        assert result["available_dumps"] == []


class TestRunCommandValidation:
    def test_requires_a_target(self, plugin):
        result = _call(plugin.run_windbg_cmd, command="lm")
        assert result["success"] is False
        assert "dump_path" in result["error"]
    
    def test_rejects_both_targets(self, plugin):
        result = _call(
            plugin.run_windbg_cmd,
            command="lm",
            dump_path="C:\\a.dmp",
            connection_string="tcp:Port=1,Server=h",
        )
        assert result["success"] is False
    
    def test_missing_dump_file_reports_failure(self, plugin, tmp_path):
        result = _call(
            plugin.run_windbg_cmd,
            command="lm",
            dump_path=str(tmp_path / "absent.dmp"),
        )
        assert result["success"] is False


class TestSessionListing:
    def test_lists_no_sessions(self, plugin):
        result = _call(plugin.list_windbg_sessions)
        assert result["success"] is True
        assert result["count"] == 0
        assert result["sessions"] == []


class TestCloseSessions:
    def test_closing_unknown_dump_session(self, plugin, tmp_path):
        result = _call(plugin.close_windbg_dump, dump_path=str(tmp_path / "x.dmp"))
        assert result["success"] is False
    
    def test_closing_unknown_remote_session(self, plugin):
        result = _call(
            plugin.close_windbg_remote, connection_string="tcp:Port=1,Server=h"
        )
        assert result["success"] is False


class TestOpenDumpErrors:
    def test_nonexistent_dump_reports_failure(self, plugin, tmp_path):
        result = _call(plugin.open_windbg_dump, dump_path=str(tmp_path / "absent.dmp"))
        assert result["success"] is False
        assert "error" in result
