"""WinDBG插件 - Semantic Kernel MCP服务

提供Windows调试工具的MCP服务接口，基于Semantic Kernel框架实现。
包含崩溃转储分析、远程调试和调试命令执行功能。

CDB命令行选项参考文档：
https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options
"""

import json
import os
import glob
import winreg
import logging
from typing import Annotated, Optional, Dict, Any
from dataclasses import asdict
from semantic_kernel.functions import kernel_function
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .cdb_session import (
    CDBSession, CDBError, CDBTimeoutError, SessionManager, SessionInfo,
    ConnectionType, SessionState, session_manager
)

#: 冷符号缓存下 ``!analyze -v`` 可能耗时数十秒，而MCP客户端的默认调用超时通常更短。
#: 该上限用于在客户端放弃前返回可用的部分结果。
DEFAULT_ANALYSIS_TIMEOUT = 45

logger = logging.getLogger(__name__)


def _serialize_session_info(session_info: SessionInfo) -> Dict[str, Any]:
    """
    将SessionInfo对象序列化为可JSON序列化的字典
    
    Args:
        session_info: SessionInfo对象
        
    Returns:
        Dict[str, Any]: 可JSON序列化的字典
    """
    data = asdict(session_info)
    # 将枚举类型转换为字符串值
    if isinstance(data.get('connection_type'), ConnectionType):
        data['connection_type'] = data['connection_type'].value
    elif 'connection_type' in data:
        # 如果已经是字符串，保持不变；如果是其他类型，转换为字符串
        if not isinstance(data['connection_type'], str):
            data['connection_type'] = str(data['connection_type'])
    
    if isinstance(data.get('state'), SessionState):
        data['state'] = data['state'].value
    elif 'state' in data:
        # 如果已经是字符串，保持不变；如果是其他类型，转换为字符串
        if not isinstance(data['state'], str):
            data['state'] = str(data['state'])
    
    return data


#: CDB默认符号路径，指向Microsoft公共符号服务器
#: https://learn.microsoft.com/windows-hardware/drivers/debugger/microsoft-public-symbols
DEFAULT_SYMBOL_PATH = "SRV*https://msdl.microsoft.com/download/symbols"


class WinDbgPluginConfig(BaseSettings):
    """
    WinDBG插件配置类
    
    ``cdb_path`` 与 ``timeout`` 从 ``CDB_PATH`` / ``DEFAULT_TIMEOUT`` 环境变量读取。
    
    ``symbol_path`` 与 ``source_path`` 不占用 ``SYMBOL_PATH`` / ``SOURCE_PATH``：
    CDB自身已经识别 ``_NT_SYMBOL_PATH`` 与 ``_NT_SOURCE_PATH``，再读一套裸名环境
    变量会与调试器原生行为产生歧义。二者只接受带 ``MCP_WINDBG_`` 前缀的命名空间
    变量，因此不会与原生变量混淆。
    
    未显式配置时，``symbol_path`` 使用Microsoft公共符号服务器以提升开箱可用性；
    显式传入空字符串则跳过 ``-y``，让CDB回落到 ``_NT_SYMBOL_PATH``。
    
    参考：
    - https://learn.microsoft.com/windows-hardware/drivers/debugger/symbol-path
    - https://learn.microsoft.com/windows-hardware/drivers/debugger/source-path
    """
    
    # env_prefix隔离按字段名推导的裸名环境变量；populate_by_name保证声明了
    # validation_alias的字段仍可用字段名直接构造（命令行参数即走这条路径）
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MCP_WINDBG_",
        populate_by_name=True,
        case_sensitive=False,
        extra="ignore",
    )
    
    cdb_path: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("cdb_path", "CDB_PATH"),
        description="Path to cdb.exe",
    )
    symbol_path: Optional[str] = Field(
        DEFAULT_SYMBOL_PATH,
        description="Symbol search path passed to CDB via -y",
    )
    source_path: Optional[str] = Field(
        None,
        description="Source search path passed to CDB via -srcpath",
    )
    timeout: int = Field(
        600,
        validation_alias=AliasChoices("timeout", "DEFAULT_TIMEOUT"),
        description="Command execution timeout in seconds",
    )


class WinDbgPlugin:
    """Windows调试工具MCP插件"""
    
    def __init__(self, config: Optional[WinDbgPluginConfig] = None):
        self.logger = logger
        self.session_manager = session_manager
        
        config = config or WinDbgPluginConfig()
        self.cdb_path = config.cdb_path
        self.symbol_path = config.symbol_path
        self.source_path = config.source_path
        self.timeout = config.timeout
    
    def set_cdb_path(self, path: str):
        """设置自定义CDB路径"""
        self.cdb_path = path
        self.logger.info(f"设置CDB路径: {path}")
    
    def set_symbol_path(self, path: str):
        """设置自定义符号路径"""
        self.symbol_path = path
        self.logger.info(f"设置符号路径: {path}")
    
    def set_source_path(self, path: str):
        """设置自定义源代码路径"""
        self.source_path = path
        self.logger.info(f"设置源代码路径: {path}")

    def set_timeout(self, timeout: int):
        """设置命令超时时间"""
        self.timeout = timeout
        self.logger.info(f"设置超时时间: {timeout}秒")
    
    def _get_local_dumps_path(self) -> Optional[str]:
        """
        从Windows注册表获取本地转储路径
        
        参考：https://learn.microsoft.com/windows/win32/wer/collecting-user-mode-dumps
        """
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps"
            ) as key:
                dump_folder, _ = winreg.QueryValueEx(key, "DumpFolder")
                dump_folder = os.path.expandvars(dump_folder)
                if os.path.isdir(dump_folder):
                    return dump_folder
        except OSError:
            # 注册表键或DumpFolder值可能不存在
            pass
        
        # 默认Windows转储位置
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            default_path = os.path.join(local_app_data, "CrashDumps")
            if os.path.isdir(default_path):
                return default_path
            
        return None
    
    def _create_session(
        self, 
        dump_path: Optional[str] = None,
        remote_connection: Optional[str] = None
    ) -> CDBSession:
        """创建CDB会话的辅助方法"""
        return self.session_manager.create_session(
            dump_path=dump_path,
            remote_connection=remote_connection,
            cdb_path=self.cdb_path,
            symbol_path=self.symbol_path,
            source_path=self.source_path,
            timeout=self.timeout
        )
    
    @kernel_function(description="Analyze a Windows crash dump file using CDB/WinDBG and return comprehensive analysis results")
    def open_windbg_dump(
        self,
        dump_path: Annotated[
            str | None,
            "Path to the crash dump file. Omit to list discoverable dumps instead.",
        ] = None,
        include_stack_trace: Annotated[
            bool, "Include the faulting thread stack trace (kb)"
        ] = False,
        include_modules: Annotated[
            bool, "Include the loaded module list (lm)"
        ] = False,
        include_threads: Annotated[
            bool, "Include the thread list (~)"
        ] = False,
        analysis_timeout: Annotated[
            int | None,
            "Seconds to wait for '!analyze -v' before returning partial results. "
            "On a cold symbol cache the first analysis can take well over a minute.",
        ] = None,
    ) -> str:
        """
        分析Windows崩溃转储文件
        
        转储文件通过CDB的 ``-z DumpFile`` 选项加载：
        https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options
        
        冷符号缓存下首次 ``!analyze -v`` 可能超过一分钟，往往长于MCP客户端的默认
        调用超时。因此该函数为自动分析设置独立上限：超时后返回已完成的部分结果并
        标记 ``partial``，会话与已下载的符号保持可用，客户端可再次调用取回完整分析。
        
        Args:
            dump_path: 崩溃转储文件路径，为空时返回可用转储列表
            include_stack_trace: 是否包含堆栈跟踪信息
            include_modules: 是否包含已加载模块信息
            include_threads: 是否包含线程信息
            analysis_timeout: ``!analyze -v`` 的等待上限（秒）
        
        Returns:
            str: JSON格式的分析结果
        """
        try:
            if not dump_path:
                # 如果未提供路径，尝试发现可用的转储文件
                local_dumps_path = self._get_local_dumps_path()
                available_dumps = []
                
                if local_dumps_path:
                    search_pattern = os.path.join(local_dumps_path, "*.*dmp")
                    dump_files = glob.glob(search_pattern)
                    
                    for dump_file in dump_files[:10]:  # 限制为10个避免混乱
                        try:
                            size_mb = round(os.path.getsize(dump_file) / (1024 * 1024), 2)
                            available_dumps.append({
                                "path": dump_file,
                                "size_mb": size_mb
                            })
                        except (OSError, IOError):
                            continue
                
                return json.dumps({
                    "success": False,
                    "message": "请提供崩溃转储文件路径进行分析",
                    "available_dumps": available_dumps,
                    "local_dumps_path": local_dumps_path
                }, ensure_ascii=False)
            
            self.logger.info(f"开始分析崩溃转储: {dump_path}")
            
            # 创建会话
            session = self._create_session(dump_path=dump_path)
            
            results = {}
            warnings: list[str] = []
            partial = False
            
            with session.progress_context("获取崩溃信息"):
                crash_info = session.send_command(".lastevent")
                results["crash_info"] = crash_info
            
            analyze_timeout = analysis_timeout or min(
                DEFAULT_ANALYSIS_TIMEOUT, self.timeout
            )
            with session.progress_context("执行自动分析"):
                try:
                    results["analysis"] = session.send_command(
                        "!analyze -v", timeout=analyze_timeout
                    )
                except CDBTimeoutError as e:
                    if not e.recovered:
                        raise
                    partial = True
                    results["analysis"] = None
                    warnings.append(
                        f"'!analyze -v' exceeded {analyze_timeout}s and was skipped. "
                        "This is expected on a cold symbol cache while symbols download. "
                        "Symbols are now cached, so retrying this call or invoking "
                        "run_windbg_cmd with '!analyze -v' should be substantially faster."
                    )
                    self.logger.warning(f"自动分析超时，返回部分结果: {dump_path}")
            
            # 可选的详细信息
            if include_stack_trace:
                with session.progress_context("获取堆栈跟踪"):
                    stack = session.send_command("kb")
                    results["stack_trace"] = stack
            
            if include_modules:
                with session.progress_context("获取模块信息"):
                    modules = session.send_command("lm")
                    results["modules"] = modules
            
            if include_threads:
                with session.progress_context("获取线程信息"):
                    threads = session.send_command("~")
                    results["threads"] = threads
            
            session_info = session.get_session_info()
            
            self.logger.info(f"崩溃转储分析完成: {dump_path}")
            
            payload = {
                "success": True,
                "partial": partial,
                "dump_path": dump_path,
                "session_id": session.session_id,
                "session_info": _serialize_session_info(session_info),
                "results": results
            }
            if warnings:
                payload["warnings"] = warnings
            return json.dumps(payload, ensure_ascii=False)
            
        except CDBError as e:
            self.logger.error(f"CDB错误: {e}")
            return json.dumps({
                "success": False,
                "error": f"CDB错误: {str(e)}",
                "timed_out": isinstance(e, CDBTimeoutError),
                "session_id": e.session_id
            }, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"分析崩溃转储失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"分析失败: {str(e)}"
            }, ensure_ascii=False)
    
    @kernel_function(description="Connect to a remote debugging session using CDB/WinDBG")
    def open_windbg_remote(
        self,
        connection_string: Annotated[
            str,
            "Remote client transport string, e.g. 'tcp:Port=5005,Server=192.168.0.100'",
        ],
        include_stack_trace: Annotated[
            bool, "Include the current thread stack trace (kb)"
        ] = False,
        include_modules: Annotated[
            bool, "Include the loaded module list (lm)"
        ] = False,
        include_threads: Annotated[
            bool, "Include the thread list (~)"
        ] = False,
    ) -> str:
        """
        连接到远程调试会话
        
        通过CDB的 ``-remote ClientTransport`` 选项建立连接：
        https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options
        
        Args:
            connection_string: 远程连接字符串（如'tcp:Port=5005,Server=192.168.0.100'）
            include_stack_trace: 是否包含堆栈跟踪信息
            include_modules: 是否包含已加载模块信息
            include_threads: 是否包含线程信息
        
        Returns:
            str: JSON格式的连接结果
        """
        try:
            self.logger.info(f"连接到远程调试会话: {connection_string}")
            
            # 创建远程会话
            session = self._create_session(remote_connection=connection_string)
            
            results = {}
            
            with session.progress_context("获取目标进程信息"):
                target_info = session.send_command("!peb")
                results["target_info"] = target_info
            
            with session.progress_context("获取当前寄存器状态"):
                current_state = session.send_command("r")
                results["registers"] = current_state
            
            # 可选的详细信息
            if include_stack_trace:
                with session.progress_context("获取堆栈跟踪"):
                    stack = session.send_command("kb")
                    results["stack_trace"] = stack
            
            if include_modules:
                with session.progress_context("获取模块信息"):
                    modules = session.send_command("lm")
                    results["modules"] = modules
            
            if include_threads:
                with session.progress_context("获取线程信息"):
                    threads = session.send_command("~")
                    results["threads"] = threads
            
            session_info = session.get_session_info()
            
            self.logger.info(f"远程调试会话已建立: {connection_string}")
            
            return json.dumps({
                "success": True,
                "connection_string": connection_string,
                "session_id": session.session_id,
                "session_info": _serialize_session_info(session_info),
                "results": results
            }, ensure_ascii=False)
            
        except CDBError as e:
            self.logger.error(f"CDB错误: {e}")
            return json.dumps({
                "success": False,
                "error": f"CDB错误: {str(e)}",
                "timed_out": isinstance(e, CDBTimeoutError),
                "session_id": e.session_id
            }, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"连接远程调试会话失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"连接失败: {str(e)}"
            }, ensure_ascii=False)
    
    @kernel_function(description="Execute a specific WinDBG/CDB command on a loaded crash dump or remote session")
    def run_windbg_cmd(
        self,
        command: Annotated[str, "WinDBG/CDB command to execute, e.g. '!analyze -v'"],
        dump_path: Annotated[
            str | None, "Crash dump file path (mutually exclusive with connection_string)"
        ] = None,
        connection_string: Annotated[
            str | None, "Remote connection string (mutually exclusive with dump_path)"
        ] = None,
        timeout: Annotated[
            int | None, "Override the command timeout in seconds"
        ] = None,
    ) -> str:
        """
        在已加载的会话上执行特定的WinDBG命令
        
        会话以 ``-noshell`` 启动，因此 ``.shell`` 命令被调试器拒绝，
        无法借由该函数在宿主机上执行任意程序：
        https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options
        
        Args:
            command: 要执行的WinDBG命令
            dump_path: 崩溃转储文件路径（与connection_string互斥）
            connection_string: 远程连接字符串（与dump_path互斥）
            timeout: 自定义超时时间
        
        Returns:
            str: JSON格式的命令执行结果
        """
        try:
            if not dump_path and not connection_string:
                return json.dumps({
                    "success": False,
                    "error": "必须提供dump_path或connection_string中的一个"
                }, ensure_ascii=False)
            
            if dump_path and connection_string:
                return json.dumps({
                    "success": False,
                    "error": "dump_path和connection_string是互斥的"
                }, ensure_ascii=False)
            
            self.logger.info(f"执行命令: {command}")
            
            # 获取或创建会话
            session = self._create_session(
                dump_path=dump_path,
                remote_connection=connection_string
            )
            
            with session.progress_context(f"执行命令: {command}"):
                output = session.send_command(command, timeout=timeout)
            
            session_info = session.get_session_info()
            
            self.logger.info(f"命令执行完成: {command}")
            
            return json.dumps({
                "success": True,
                "command": command,
                "session_id": session.session_id,
                "session_info": _serialize_session_info(session_info),
                "output": output
            }, ensure_ascii=False)
            
        except CDBTimeoutError as e:
            self.logger.warning(f"命令超时: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
                "timed_out": True,
                "session_recovered": e.recovered,
                "command": command,
                "session_id": e.session_id
            }, ensure_ascii=False)
        except CDBError as e:
            self.logger.error(f"CDB错误: {e}")
            return json.dumps({
                "success": False,
                "error": f"CDB错误: {str(e)}",
                "timed_out": False,
                "command": command,
                "session_id": e.session_id
            }, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"执行命令失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"执行失败: {str(e)}",
                "command": command
            }, ensure_ascii=False)
    
    @kernel_function(description="Close and unload a crash dump session to free up resources")
    def close_windbg_dump(
        self,
        dump_path: Annotated[str, "Crash dump file path of the session to close"],
    ) -> str:
        """
        关闭并卸载崩溃转储会话
        
        Args:
            dump_path: 要关闭的崩溃转储文件路径
        
        Returns:
            str: JSON格式的操作结果
        """
        try:
            session_id = os.path.abspath(dump_path)
            success = self.session_manager.close_session(session_id)
            
            if success:
                self.logger.info(f"成功关闭崩溃转储会话: {dump_path}")
                return json.dumps({
                    "success": True,
                    "message": f"成功关闭崩溃转储会话: {dump_path}",
                    "session_id": session_id
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "success": False,
                    "message": f"未找到活跃的崩溃转储会话: {dump_path}",
                    "session_id": session_id
                }, ensure_ascii=False)
                
        except Exception as e:
            self.logger.error(f"关闭崩溃转储会话失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"关闭失败: {str(e)}"
            }, ensure_ascii=False)
    
    @kernel_function(description="Close a remote debugging connection and free up resources")
    def close_windbg_remote(
        self,
        connection_string: Annotated[str, "Remote connection string of the session to close"],
    ) -> str:
        """
        关闭远程调试连接
        
        Args:
            connection_string: 要关闭的远程连接字符串
        
        Returns:
            str: JSON格式的操作结果
        """
        try:
            session_id = f"remote:{connection_string}"
            success = self.session_manager.close_session(session_id)
            
            if success:
                self.logger.info(f"成功关闭远程连接: {connection_string}")
                return json.dumps({
                    "success": True,
                    "message": f"成功关闭远程连接: {connection_string}",
                    "session_id": session_id
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "success": False,
                    "message": f"未找到活跃的远程连接: {connection_string}",
                    "session_id": session_id
                }, ensure_ascii=False)
                
        except Exception as e:
            self.logger.error(f"关闭远程连接失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"关闭失败: {str(e)}"
            }, ensure_ascii=False)
    
    @kernel_function(description="List Windows crash dump files in a specified directory")
    def list_windbg_dumps(
        self,
        directory_path: Annotated[
            str | None,
            "Directory to search. Defaults to the WER LocalDumps folder or %LOCALAPPDATA%\\CrashDumps.",
        ] = None,
        recursive: Annotated[bool, "Search subdirectories recursively"] = False,
    ) -> str:
        """
        列出指定目录中的Windows崩溃转储文件
        
        Args:
            directory_path: 搜索转储文件的目录路径（如未指定将使用注册表配置的路径）
            recursive: 是否递归搜索子目录
        
        Returns:
            str: JSON格式的转储文件列表
        """
        try:
            if directory_path is None:
                directory_path = self._get_local_dumps_path()
                if directory_path is None:
                    return json.dumps({
                        "success": False,
                        "error": "未指定目录路径且在注册表中未找到默认转储路径"
                    }, ensure_ascii=False)
            
            if not os.path.exists(directory_path) or not os.path.isdir(directory_path):
                return json.dumps({
                    "success": False,
                    "error": f"目录未找到: {directory_path}"
                }, ensure_ascii=False)
            
            self.logger.info(f"搜索转储文件: {directory_path} (递归: {recursive})")
            
            # 确定搜索模式
            search_pattern = os.path.join(
                directory_path, 
                "**" if recursive else "", 
                "*.*dmp"
            )
            
            # 查找所有转储文件
            dump_files = glob.glob(search_pattern, recursive=recursive)
            dump_files.sort()  # 按字母顺序排序
            
            if not dump_files:
                return json.dumps({
                    "success": True,
                    "message": f"在{directory_path}中未找到崩溃转储文件(*.*dmp)",
                    "directory_path": directory_path,
                    "dump_files": []
                }, ensure_ascii=False)
            
            # 格式化结果
            dump_info = []
            for dump_file in dump_files:
                try:
                    stat_info = os.stat(dump_file)
                    size_mb = round(stat_info.st_size / (1024 * 1024), 2)
                    
                    dump_info.append({
                        "path": dump_file,
                        "size_mb": size_mb,
                        "modified_time": stat_info.st_mtime
                    })
                except (OSError, IOError):
                    dump_info.append({
                        "path": dump_file,
                        "size_mb": "unknown",
                        "modified_time": None
                    })
            
            self.logger.info(f"找到{len(dump_files)}个转储文件")
            
            return json.dumps({
                "success": True,
                "directory_path": directory_path,
                "recursive": recursive,
                "count": len(dump_files),
                "dump_files": dump_info
            }, ensure_ascii=False)
            
        except Exception as e:
            self.logger.error(f"列出转储文件失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"列出失败: {str(e)}"
            }, ensure_ascii=False)
    
    @kernel_function(description="List all active CDB debugging sessions and their status")
    def list_windbg_sessions(self) -> str:
        """
        列出所有活跃的CDB调试会话及其状态
        
        Returns:
            str: JSON格式的会话列表
        """
        try:
            # 清理死会话与空闲超时会话
            self.session_manager.cleanup_dead_sessions()
            self.session_manager.cleanup_idle_sessions()
            
            # 获取所有会话信息
            sessions_info = self.session_manager.list_sessions()
            
            sessions_data = []
            for session_info in sessions_info:
                sessions_data.append(_serialize_session_info(session_info))
            
            self.logger.info(f"列出{len(sessions_data)}个活跃会话")
            
            return json.dumps({
                "success": True,
                "count": len(sessions_data),
                "sessions": sessions_data
            }, ensure_ascii=False)
            
        except Exception as e:
            self.logger.error(f"列出会话失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"列出失败: {str(e)}"
            }, ensure_ascii=False)