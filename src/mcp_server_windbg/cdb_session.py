"""改进的CDB会话管理模块

提供增强的CDB调试会话管理功能，包括：
- 详细的日志记录和进度跟踪
- 会话状态管理
- 命令执行超时控制
- 错误处理和重试机制

CDB命令行选项参考文档：
https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options
"""

import shutil
import subprocess
import threading
import re
import os
import platform
import time
import logging
import locale
from typing import List, Optional, Dict
from enum import Enum
from dataclasses import dataclass
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 命令标记用于可靠地检测命令完成
COMMAND_MARKER = ".echo COMMAND_COMPLETED_MARKER"
COMMAND_MARKER_PATTERN = re.compile(r"COMMAND_COMPLETED_MARKER")

# 默认的CDB.exe可能位置
DEFAULT_CDB_PATHS = [
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\arm64\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\cdb.exe",
    r"C:\Program Files\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files\Debugging Tools for Windows (x64)\cdb.exe",
    r"C:\Program Files\Debugging Tools for Windows (x86)\cdb.exe",
]


def _get_system_encoding() -> str:
    """
    智能检测CDB子进程输出所使用的编码
    
    Returns:
        str: 检测到的编码名称，检测失败时回退到utf-8
    """
    if platform.system() != "Windows":
        return "utf-8"
    
    import codecs
    
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
        if cp:
            encoding = f"cp{cp}"
            codecs.lookup(encoding)
            logger.info(f"检测到Windows控制台编码: {encoding}")
            return encoding
    except (AttributeError, LookupError, OSError, ValueError):
        pass
    
    try:
        encoding = locale.getpreferredencoding(False)
        if encoding:
            codecs.lookup(encoding)
            logger.info(f"检测到系统首选编码: {encoding}")
            return encoding
    except (LookupError, ValueError):
        pass
    
    logger.warning("编码检测失败，回退到utf-8")
    return "utf-8"


class SessionState(Enum):
    """会话状态枚举"""
    INITIALIZING = "initializing"
    READY = "ready"
    EXECUTING = "executing"
    ERROR = "error"
    CLOSED = "closed"


class ConnectionType(Enum):
    """连接类型枚举"""
    DUMP_FILE = "dump_file"
    REMOTE_DEBUG = "remote_debug"


@dataclass
class SessionInfo:
    """会话信息"""
    session_id: str
    connection_type: ConnectionType
    target: str  # 转储文件路径或远程连接字符串
    state: SessionState
    created_at: float
    last_activity: float
    commands_executed: int
    cdb_path: str
    symbol_path: Optional[str] = None
    source_path: Optional[str] = None


class CDBError(Exception):
    """CDB相关错误的自定义异常"""
    
    def __init__(self, message: str, session_id: Optional[str] = None):
        super().__init__(message)
        self.session_id = session_id


class CDBSession:
    """增强的CDB调试会话管理器"""
    
    def __init__(
        self,
        dump_path: Optional[str] = None,
        remote_connection: Optional[str] = None,
        cdb_path: Optional[str] = None,
        symbol_path: Optional[str] = None,
        source_path: Optional[str] = None,
        initial_commands: Optional[List[str]] = None,
        timeout: int = 600,
        additional_args: Optional[List[str]] = None
    ):
        """
        初始化CDB调试会话
        
        Args:
            dump_path: 崩溃转储文件路径（与remote_connection互斥）
            remote_connection: 远程调试连接字符串
            cdb_path: 自定义CDB.exe路径
            symbol_path: 自定义符号路径
            source_path: 自定义源代码路径
            initial_commands: CDB启动时运行的初始命令
            timeout: 命令超时时间（秒）
            additional_args: 传递给CDB.exe的额外参数
        """
        # 验证参数
        if not dump_path and not remote_connection:
            raise ValueError("必须提供dump_path或remote_connection中的一个")
        if dump_path and remote_connection:
            raise ValueError("dump_path和remote_connection是互斥的")
            
        if dump_path and not os.path.isfile(dump_path):
            raise FileNotFoundError(f"转储文件未找到: {dump_path}")
            
        self.dump_path = dump_path
        self.remote_connection = remote_connection
        self.timeout = timeout
        
        # 确定连接类型和会话ID
        if self.dump_path:
            self.connection_type = ConnectionType.DUMP_FILE
            self.session_id = os.path.abspath(self.dump_path)
            self.target = self.dump_path
        else:
            self.connection_type = ConnectionType.REMOTE_DEBUG
            self.session_id = f"remote:{self.remote_connection}"
            self.target = self.remote_connection
        
        # 查找CDB可执行文件
        self.cdb_path = self._find_cdb_executable(cdb_path)
        if not self.cdb_path:
            raise CDBError("找不到cdb.exe。请提供有效路径。", self.session_id)
        
        self.symbol_path = symbol_path
        self.source_path = source_path
        
        # 会话状态
        self.state = SessionState.INITIALIZING
        self.created_at = time.time()
        self.last_activity = self.created_at
        self.commands_executed = 0
        
        # 线程同步
        self.output_lines = []
        self.lock = threading.Lock()
        self.ready_event = threading.Event()
        self.process: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        
        logger.info(f"初始化CDB会话: {self.session_id}")
        logger.info(f"连接类型: {self.connection_type.value}")
        logger.info(f"CDB路径: {self.cdb_path}")
        
        try:
            self._start_cdb_process(additional_args)
            self._wait_for_initialization()
            
            # 运行初始命令
            if initial_commands:
                logger.info(f"执行{len(initial_commands)}个初始命令")
                for cmd in initial_commands:
                    self.send_command(cmd)
                    
            self.state = SessionState.READY
            logger.info(f"CDB会话已就绪: {self.session_id}")
            
        except Exception as e:
            self.state = SessionState.ERROR
            self.shutdown()
            raise CDBError(f"CDB会话初始化失败: {str(e)}", self.session_id)
    
    def _find_cdb_executable(self, custom_path: Optional[str] = None) -> Optional[str]:
        """查找CDB可执行文件"""
        if custom_path and os.path.isfile(custom_path):
            logger.info(f"使用自定义CDB路径: {custom_path}")
            return custom_path
            
        # 在Windows上尝试默认路径
        if platform.system() == "Windows":
            for path in DEFAULT_CDB_PATHS:
                if os.path.isfile(path):
                    logger.info(f"找到CDB: {path}")
                    return path
            
            resolved = shutil.which("cdb")
            if resolved:
                logger.info(f"从PATH找到CDB: {resolved}")
                return resolved
        
        logger.error("未找到CDB可执行文件")
        return None
    
    def _start_cdb_process(self, additional_args: Optional[List[str]] = None):
        """
        启动CDB进程
        
        命令行选项参考：
        https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options
        
        使用的选项：
        - ``-z DumpFile``: 加载崩溃转储文件
        - ``-remote ClientTransport``: 连接到已运行的调试服务器，必须位于首位
        - ``-y SymbolPath``: 符号搜索路径
        - ``-srcpath SourcePath``: 源文件搜索路径
        - ``-noshell``: 禁用 ``.shell`` 命令，阻止通过调试器执行宿主机命令
        """
        cmd_args = [self.cdb_path]
        
        # -remote必须是命令行上的第一个参数
        if self.remote_connection:
            cmd_args.extend(["-remote", self.remote_connection])
            logger.info(f"连接到远程目标: {self.remote_connection}")
        
        # 禁用.shell，避免通过调试命令逃逸到宿主机shell
        cmd_args.append("-noshell")
        
        # 添加符号路径 https://learn.microsoft.com/windows-hardware/drivers/debugger/symbol-path
        if self.symbol_path:
            cmd_args.extend(["-y", self.symbol_path])
            logger.info(f"使用符号路径: {self.symbol_path}")
        
        # 添加源代码路径 https://learn.microsoft.com/windows-hardware/drivers/debugger/source-path
        if self.source_path:
            cmd_args.extend(["-srcpath", self.source_path])
            logger.info(f"使用源代码路径: {self.source_path}")
        
        # 添加额外参数
        if additional_args:
            cmd_args.extend(additional_args)
            logger.info(f"添加额外参数: {additional_args}")
        
        # -z必须在选项之后，作为目标说明符
        if self.dump_path:
            cmd_args.extend(["-z", self.dump_path])
            logger.info(f"加载转储文件: {self.dump_path}")
        
        try:
            logger.info(f"启动CDB进程: {' '.join(cmd_args)}")
            # 使用errors='replace'来处理编码错误
            encoding = _get_system_encoding()
            self.process = subprocess.Popen(
                cmd_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding=encoding,
                errors='replace',
                bufsize=1
            )
            
            # 启动输出读取线程
            self.reader_thread = threading.Thread(target=self._read_output)
            self.reader_thread.daemon = True
            self.reader_thread.start()
            
        except Exception as e:
            raise CDBError(f"启动CDB进程失败: {str(e)}", self.session_id)
    
    def _read_output(self):
        """线程函数：持续读取CDB输出"""
        if not self.process or not self.process.stdout:
            return
        
        buffer = []
        try:
            for line in self.process.stdout:
                try:
                    line = line.rstrip()
                    logger.debug(f"CDB输出: {line}")
                    
                    with self.lock:
                        buffer.append(line)
                        # 检查是否包含命令完成标记
                        if COMMAND_MARKER_PATTERN.search(line):
                            # 移除标记行本身
                            if buffer and COMMAND_MARKER_PATTERN.search(buffer[-1]):
                                buffer.pop()
                            self.output_lines = buffer
                            buffer = []
                            self.ready_event.set()
                except (UnicodeDecodeError, UnicodeError) as e:
                    # 如果遇到编码错误，记录警告但继续处理
                    logger.warning(f"CDB输出编码错误，跳过该行: {e}")
                    continue
                        
        except (IOError, ValueError) as e:
            logger.error(f"CDB输出读取错误: {e}")
        except Exception as e:
            logger.error(f"CDB输出读取意外错误: {e}")
    
    def _wait_for_initialization(self):
        """等待CDB初始化完成"""
        logger.info("等待CDB初始化...")
        try:
            self.ready_event.clear()
            self.process.stdin.write(f"{COMMAND_MARKER}\n")
            self.process.stdin.flush()
            
            if not self.ready_event.wait(timeout=self.timeout):
                raise CDBError("CDB初始化超时", self.session_id)
                
            logger.info("CDB初始化完成")
        except IOError as e:
            raise CDBError(f"CDB通信失败: {str(e)}", self.session_id)
    
    def send_command(self, command: str, timeout: Optional[int] = None) -> List[str]:
        """
        发送命令到CDB并返回输出
        
        Args:
            command: 要发送的命令
            timeout: 自定义超时时间（覆盖实例超时）
            
        Returns:
            CDB输出行列表
            
        Raises:
            CDBError: 如果命令超时或CDB无响应
        """
        if not self.process:
            raise CDBError("CDB进程未运行", self.session_id)
        
        if self.state != SessionState.READY:
            raise CDBError(f"会话状态不正确: {self.state.value}", self.session_id)
            
        logger.info(f"执行命令: {command}")
        self.state = SessionState.EXECUTING
        self.last_activity = time.time()
        
        self.ready_event.clear()
        with self.lock:
            self.output_lines = []
            
        try:
            # 发送命令和完成标记
            self.process.stdin.write(f"{command}\n{COMMAND_MARKER}\n")
            self.process.stdin.flush()
        except IOError as e:
            self.state = SessionState.ERROR
            raise CDBError(f"发送命令失败: {str(e)}", self.session_id)
            
        cmd_timeout = timeout or self.timeout
        logger.debug(f"等待命令完成，超时: {cmd_timeout}秒")
        
        if not self.ready_event.wait(timeout=cmd_timeout):
            self.state = SessionState.ERROR
            raise CDBError(f"命令超时 ({cmd_timeout}秒): {command}", self.session_id)
            
        with self.lock:
            result = self.output_lines.copy()
            self.output_lines = []
        
        self.commands_executed += 1
        self.state = SessionState.READY
        self.last_activity = time.time()
        
        logger.info(f"命令完成，返回{len(result)}行输出")
        return result
    
    def get_session_info(self) -> SessionInfo:
        """获取会话信息"""
        return SessionInfo(
            session_id=self.session_id,
            connection_type=self.connection_type,
            target=self.target,
            state=self.state,
            created_at=self.created_at,
            last_activity=self.last_activity,
            commands_executed=self.commands_executed,
            cdb_path=self.cdb_path,
            symbol_path=self.symbol_path,
            source_path=self.source_path
        )
    
    def is_alive(self) -> bool:
        """检查会话是否活跃"""
        return (self.process is not None and 
                self.process.poll() is None and 
                self.state not in [SessionState.ERROR, SessionState.CLOSED])
    
    @contextmanager
    def progress_context(self, description: str):
        """进度上下文管理器，用于长时间运行的操作"""
        logger.info(f"开始: {description}")
        start_time = time.time()
        try:
            yield
        finally:
            elapsed = time.time() - start_time
            logger.info(f"完成: {description} (耗时: {elapsed:.2f}秒)")
    
    def shutdown(self):
        """清理并终止CDB进程"""
        logger.info(f"关闭CDB会话: {self.session_id}")
        self.state = SessionState.CLOSED
        
        try:
            if self.process and self.process.poll() is None:
                try:
                    if self.remote_connection:
                        # 远程连接发送CTRL+B分离
                        logger.debug("发送分离命令到远程会话")
                        self.process.stdin.write("\x02")  # CTRL+B
                        self.process.stdin.flush()
                    else:
                        # 转储文件发送'q'退出
                        logger.debug("发送退出命令")
                        self.process.stdin.write("q\n")
                        self.process.stdin.flush()
                    self.process.wait(timeout=3)
                except Exception as e:
                    logger.warning(f"优雅关闭失败: {e}")
                
                if self.process.poll() is None:
                    logger.warning("强制终止CDB进程")
                    self.process.terminate()
                    self.process.wait(timeout=5)
        except Exception as e:
            logger.error(f"关闭期间出错: {e}")
        finally:
            self.process = None
    
    def __enter__(self):
        """上下文管理器协议支持"""
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器时清理"""
        self.shutdown()


class SessionManager:
    """CDB会话管理器"""
    
    def __init__(self):
        self.sessions: Dict[str, CDBSession] = {}
        self.lock = threading.Lock()
        
    def create_session(
        self,
        dump_path: Optional[str] = None,
        remote_connection: Optional[str] = None,
        **kwargs
    ) -> CDBSession:
        """创建新的CDB会话"""
        # 确定会话ID
        if dump_path:
            session_id = os.path.abspath(dump_path)
        else:
            session_id = f"remote:{remote_connection}"
        
        with self.lock:
            # 检查是否已存在活跃会话
            if session_id in self.sessions:
                existing_session = self.sessions[session_id]
                if existing_session.is_alive():
                    logger.info(f"返回现有会话: {session_id}")
                    return existing_session
                else:
                    # 清理死会话
                    logger.info(f"清理死会话: {session_id}")
                    existing_session.shutdown()
                    del self.sessions[session_id]
            
            # 创建新会话
            session = CDBSession(
                dump_path=dump_path,
                remote_connection=remote_connection,
                **kwargs
            )
            self.sessions[session_id] = session
            logger.info(f"创建新会话: {session_id}")
            return session
    
    def get_session(self, session_id: str) -> Optional[CDBSession]:
        """获取指定会话"""
        with self.lock:
            return self.sessions.get(session_id)
    
    def close_session(self, session_id: str) -> bool:
        """关闭指定会话"""
        with self.lock:
            session = self.sessions.get(session_id)
            if session:
                session.shutdown()
                del self.sessions[session_id]
                logger.info(f"会话已关闭: {session_id}")
                return True
            return False
    
    def list_sessions(self) -> List[SessionInfo]:
        """列出所有会话信息"""
        with self.lock:
            return [session.get_session_info() for session in self.sessions.values()]
    
    def cleanup_dead_sessions(self):
        """清理死会话"""
        with self.lock:
            dead_sessions = [
                session_id for session_id, session in self.sessions.items()
                if not session.is_alive()
            ]
            
            for session_id in dead_sessions:
                logger.info(f"清理死会话: {session_id}")
                self.sessions[session_id].shutdown()
                del self.sessions[session_id]
    
    def shutdown_all(self):
        """关闭所有会话"""
        with self.lock:
            for session in self.sessions.values():
                session.shutdown()
            self.sessions.clear()
            logger.info("所有会话已关闭")


# 全局会话管理器实例
session_manager = SessionManager()