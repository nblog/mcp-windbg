"""CDB会话层测试

进程无关的部分（编码探测、argv构造、标记序号、会话回收）使用替身对象，
真实调试器交互的测试标记为 ``requires_cdb``。
"""

import platform
import subprocess
import time
from unittest.mock import patch

import pytest

from mcp_server_windbg import cdb_session
from mcp_server_windbg.cdb_session import (
    COMMAND_MARKER_PATTERN,
    CDBError,
    CDBSession,
    CDBTimeoutError,
    SessionInfo,
    SessionManager,
    SessionState,
    _get_system_encoding,
    _marker_command,
)


class TestSystemEncoding:
    """编码探测曾在函数末尾走空返回None，使Popen(encoding=None)行为退化"""
    
    def test_always_returns_a_usable_codec(self):
        import codecs
        
        encoding = _get_system_encoding()
        assert isinstance(encoding, str)
        codecs.lookup(encoding)
    
    def test_falls_back_when_console_and_locale_fail(self):
        """两条探测路径都失败时必须返回utf-8，而不是隐式返回None"""
        with patch.object(cdb_session.platform, "system", return_value="Windows"):
            with patch.dict("sys.modules", {"ctypes": None}):
                with patch.object(
                    cdb_session.locale, "getpreferredencoding", side_effect=LookupError
                ):
                    assert _get_system_encoding() == "utf-8"
    
    def test_non_windows_uses_utf8(self):
        with patch.object(cdb_session.platform, "system", return_value="Linux"):
            assert _get_system_encoding() == "utf-8"


class TestCommandMarker:
    def test_marker_command_carries_sequence(self):
        assert _marker_command(7) == ".echo COMMAND_COMPLETED_MARKER_7"
    
    def test_pattern_extracts_sequence(self):
        match = COMMAND_MARKER_PATTERN.search("0:000> COMMAND_COMPLETED_MARKER_12")
        assert match and match.group(1) == "12"
    
    def test_pattern_ignores_unrelated_output(self):
        assert COMMAND_MARKER_PATTERN.search("ntdll!NtWaitForSingleObject") is None


class TestSessionValidation:
    def test_requires_a_target(self):
        with pytest.raises(ValueError):
            CDBSession()
    
    def test_target_arguments_are_mutually_exclusive(self):
        with pytest.raises(ValueError):
            CDBSession(dump_path="C:\\a.dmp", remote_connection="tcp:Port=1,Server=h")
    
    def test_missing_dump_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            CDBSession(dump_path=str(tmp_path / "absent.dmp"))


class TestProcessArguments:
    """CDB命令行选项顺序
    
    ``-remote`` 必须位于所有选项之前，``-z`` 作为目标说明符应排在选项之后。
    https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options
    """
    
    @staticmethod
    def _capture_argv(**kwargs):
        captured = {}
        
        def fake_popen(args, **_):
            captured["argv"] = args
            raise OSError("stop before spawning")
        
        session = CDBSession.__new__(CDBSession)
        session.session_id = "test"
        session.cdb_path = "cdb.exe"
        session.dump_path = kwargs.get("dump_path")
        session.remote_connection = kwargs.get("remote_connection")
        session.symbol_path = kwargs.get("symbol_path")
        session.source_path = kwargs.get("source_path")
        
        with patch.object(subprocess, "Popen", side_effect=fake_popen):
            with pytest.raises(CDBError):
                session._start_cdb_process(kwargs.get("additional_args"))
        return captured["argv"]
    
    def test_dump_target_comes_last(self):
        argv = self._capture_argv(dump_path="C:\\a.dmp", symbol_path="C:\\sym")
        assert argv[-2:] == ["-z", "C:\\a.dmp"]
    
    def test_remote_flag_comes_first(self):
        argv = self._capture_argv(
            remote_connection="tcp:Port=1,Server=h", symbol_path="C:\\sym"
        )
        assert argv[1:3] == ["-remote", "tcp:Port=1,Server=h"]
    
    def test_noshell_is_always_present(self):
        """-noshell 阻止通过 .shell 从调试命令逃逸到宿主机"""
        argv = self._capture_argv(dump_path="C:\\a.dmp")
        assert "-noshell" in argv
    
    def test_symbol_path_uses_y_flag(self):
        argv = self._capture_argv(dump_path="C:\\a.dmp", symbol_path="C:\\sym")
        assert argv[argv.index("-y") + 1] == "C:\\sym"
    
    def test_source_path_uses_srcpath_flag(self):
        argv = self._capture_argv(dump_path="C:\\a.dmp", source_path="C:\\src")
        assert argv[argv.index("-srcpath") + 1] == "C:\\src"
    
    def test_empty_symbol_path_is_omitted(self):
        """空符号路径让CDB回落到 _NT_SYMBOL_PATH"""
        argv = self._capture_argv(dump_path="C:\\a.dmp", symbol_path="")
        assert "-y" not in argv


def _make_session_info(session_id: str, last_activity: float) -> SessionInfo:
    return SessionInfo(
        session_id=session_id,
        connection_type=cdb_session.ConnectionType.DUMP_FILE,
        target=session_id,
        state=SessionState.READY,
        created_at=last_activity,
        last_activity=last_activity,
        commands_executed=0,
        cdb_path="cdb.exe",
    )


class FakeSession:
    """SessionManager测试替身"""
    
    def __init__(self, session_id: str, last_activity: float, alive: bool = True):
        self.session_id = session_id
        self.last_activity = last_activity
        self._alive = alive
        self.shutdown_called = False
    
    def is_alive(self):
        return self._alive
    
    def shutdown(self):
        self.shutdown_called = True
        self._alive = False
    
    def get_session_info(self):
        return _make_session_info(self.session_id, self.last_activity)


class TestIdleReaping:
    """空闲会话回收
    
    每个存活会话都持有一个CDB进程及其转储映射，长时间空闲会白占内存。
    """
    
    def test_reclaims_sessions_past_the_threshold(self):
        manager = SessionManager(idle_timeout=100)
        stale = FakeSession("stale", time.time() - 500)
        fresh = FakeSession("fresh", time.time())
        manager.sessions = {"stale": stale, "fresh": fresh}
        
        reclaimed = manager.cleanup_idle_sessions()
        
        assert reclaimed == ["stale"]
        assert stale.shutdown_called is True
        assert set(manager.sessions) == {"fresh"}
    
    def test_zero_threshold_disables_reclamation(self):
        manager = SessionManager(idle_timeout=0)
        stale = FakeSession("stale", 0.0)
        manager.sessions = {"stale": stale}
        
        assert manager.cleanup_idle_sessions() == []
        assert stale.shutdown_called is False
    
    def test_explicit_threshold_overrides_instance_value(self):
        manager = SessionManager(idle_timeout=10_000)
        manager.sessions = {"stale": FakeSession("stale", time.time() - 50)}
        
        assert manager.cleanup_idle_sessions(idle_timeout=10) == ["stale"]
    
    def test_dead_sessions_are_cleaned(self):
        manager = SessionManager(idle_timeout=0)
        dead = FakeSession("dead", time.time(), alive=False)
        manager.sessions = {"dead": dead}
        
        manager.cleanup_dead_sessions()
        
        assert manager.sessions == {}
        assert dead.shutdown_called is True
    
    def test_reaper_thread_is_not_started_when_disabled(self):
        manager = SessionManager(idle_timeout=0)
        manager.start_idle_reaper()
        assert manager._reaper_thread is None
    
    def test_reaper_thread_starts_once(self):
        manager = SessionManager(idle_timeout=100, reaper_interval=3600)
        try:
            manager.start_idle_reaper()
            first = manager._reaper_thread
            manager.start_idle_reaper()
            assert manager._reaper_thread is first
            assert first.daemon is True
        finally:
            manager.stop_idle_reaper()
    
    def test_shutdown_all_closes_everything(self):
        manager = SessionManager(idle_timeout=0)
        sessions = {name: FakeSession(name, time.time()) for name in ("a", "b")}
        manager.sessions = dict(sessions)
        
        manager.shutdown_all()
        
        assert manager.sessions == {}
        assert all(s.shutdown_called for s in sessions.values())


@pytest.mark.requires_cdb
@pytest.mark.skipif(platform.system() != "Windows", reason="CDB仅在Windows上可用")
class TestRealDebugger:
    """需要真实cdb.exe与转储文件的端到端行为"""
    
    @pytest.fixture
    def session(self, cdb_path, real_dump):
        instance = CDBSession(
            dump_path=real_dump, cdb_path=cdb_path, symbol_path="", timeout=120
        )
        yield instance
        instance.shutdown()
    
    def test_session_reaches_ready(self, session):
        assert session.state is SessionState.READY
        assert session.is_alive() is True
    
    def test_shell_command_is_disabled(self, session):
        """-noshell 必须让 .shell 被调试器拒绝而非执行"""
        output = "\n".join(session.send_command(".shell dir"))
        assert "disabled" in output.lower()
    
    def test_command_output_is_returned(self, session):
        assert any("Last event" in line for line in session.send_command(".lastevent"))
    
    def test_timeout_preserves_a_healthy_session(self, session):
        """超时不应销毁会话：重新同步后已加载的转储与符号仍可复用"""
        with pytest.raises(CDBTimeoutError) as excinfo:
            session.send_command("!analyze -v", timeout=1)
        
        assert excinfo.value.recovered is True
        assert session.state is SessionState.READY
        # 迟到的分析输出必须被丢弃，不能污染后续命令
        assert any("Last event" in line for line in session.send_command(".lastevent"))
