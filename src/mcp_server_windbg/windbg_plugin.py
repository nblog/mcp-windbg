"""WinDBG插件 - Semantic Kernel MCP服务

提供Windows调试工具的MCP服务接口，基于Semantic Kernel框架实现。
包含崩溃转储分析、远程调试和调试命令执行功能。
"""

import json
import os
import glob
import winreg
import logging
from typing import List, Optional, Dict, Any
from dataclasses import asdict
from semantic_kernel.functions import kernel_function
from pydantic import Field
from pydantic_settings import BaseSettings

from .cdb_session import (
    CDBSession, CDBError, SessionManager, SessionInfo, 
    ConnectionType, SessionState, session_manager
)

logger = logging.getLogger(__name__)


class WinDbgPluginConfig(BaseSettings):
    """WinDBG插件配置类，支持从环境变量读取配置"""
    
    cdb_path: Optional[str] = Field(None, env="CDB_PATH", description="CDB.exe的路径")
    symbols_path: Optional[str] = Field(None, env="SYMBOLS_PATH", description="符号文件路径")
    timeout: int = Field(600, env="DEFAULT_TIMEOUT", description="命令执行超时时间（秒）")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


class WinDbgPlugin:
    """Windows调试工具MCP插件"""
    
    def __init__(self, config: Optional[WinDbgPluginConfig] = None):
        self.logger = logger
        self.session_manager = session_manager
        
        # 如果提供了配置，使用配置中的值，否则使用默认值
        if config:
            self.cdb_path = config.cdb_path
            self.symbols_path = config.symbols_path
            self.timeout = config.timeout
        else:
            self.cdb_path = None
            self.symbols_path = None
            self.timeout = 30
        
    def set_cdb_path(self, path: str):
        """设置自定义CDB路径"""
        self.cdb_path = path
        self.logger.info(f"设置CDB路径: {path}")
        
    def set_symbols_path(self, path: str):
        """设置自定义符号路径"""
        self.symbols_path = path
        self.logger.info(f"设置符号路径: {path}")
        
    def set_timeout(self, timeout: int):
        """设置命令超时时间"""
        self.timeout = timeout
        self.logger.info(f"设置超时时间: {timeout}秒")
    
    def _get_local_dumps_path(self) -> Optional[str]:
        """从Windows注册表获取本地转储路径"""
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\\Microsoft\\Windows\\Windows Error Reporting\\LocalDumps"
            ) as key:
                dump_folder, _ = winreg.QueryValueEx(key, "DumpFolder")
                if os.path.exists(dump_folder) and os.path.isdir(dump_folder):
                    return dump_folder
        except (OSError, WindowsError):
            # 注册表键可能不存在
            pass
        
        # 默认Windows转储位置
        default_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CrashDumps")
        if os.path.exists(default_path) and os.path.isdir(default_path):
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
            symbols_path=self.symbols_path,
            timeout=self.timeout
        )
    
    @kernel_function(description="Analyze a Windows crash dump file using CDB/WinDBG and return comprehensive analysis results")
    def open_windbg_dump(
        self,
        dump_path: str,
        include_stack_trace: bool = False,
        include_modules: bool = False,
        include_threads: bool = False
    ) -> str:
        """
        分析Windows崩溃转储文件
        
        Args:
            dump_path: 崩溃转储文件路径
            include_stack_trace: 是否包含堆栈跟踪信息
            include_modules: 是否包含已加载模块信息
            include_threads: 是否包含线程信息
        
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
                }, ensure_ascii=False, indent=2)
            
            self.logger.info(f"开始分析崩溃转储: {dump_path}")
            
            # 创建会话
            session = self._create_session(dump_path=dump_path)
            
            results = {}
            
            with session.progress_context("获取崩溃信息"):
                crash_info = session.send_command(".lastevent")
                results["crash_info"] = crash_info
            
            with session.progress_context("执行自动分析"):
                analysis = session.send_command("!analyze -v")
                results["analysis"] = analysis
            
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
            
            return json.dumps({
                "success": True,
                "dump_path": dump_path,
                "session_id": session.session_id,
                "session_info": asdict(session_info),
                "results": results
            }, ensure_ascii=False, indent=2)
            
        except CDBError as e:
            self.logger.error(f"CDB错误: {e}")
            return json.dumps({
                "success": False,
                "error": f"CDB错误: {str(e)}",
                "session_id": getattr(e, 'session_id', None)
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"分析崩溃转储失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"分析失败: {str(e)}"
            }, ensure_ascii=False, indent=2)
    
    @kernel_function(description="Connect to a remote debugging session using CDB/WinDBG")
    def open_windbg_remote(
        self,
        connection_string: str,
        include_stack_trace: bool = False,
        include_modules: bool = False,
        include_threads: bool = False
    ) -> str:
        """
        连接到远程调试会话
        
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
                "session_info": asdict(session_info),
                "results": results
            }, ensure_ascii=False, indent=2)
            
        except CDBError as e:
            self.logger.error(f"CDB错误: {e}")
            return json.dumps({
                "success": False,
                "error": f"CDB错误: {str(e)}",
                "session_id": getattr(e, 'session_id', None)
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"连接远程调试会话失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"连接失败: {str(e)}"
            }, ensure_ascii=False, indent=2)
    
    @kernel_function(description="Execute a specific WinDBG/CDB command on a loaded crash dump or remote session")
    def run_windbg_cmd(
        self,
        command: str,
        dump_path: Optional[str] = None,
        connection_string: Optional[str] = None,
        timeout: Optional[int] = None
    ) -> str:
        """
        在已加载的会话上执行特定的WinDBG命令
        
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
                }, ensure_ascii=False, indent=2)
            
            if dump_path and connection_string:
                return json.dumps({
                    "success": False,
                    "error": "dump_path和connection_string是互斥的"
                }, ensure_ascii=False, indent=2)
            
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
                "session_info": asdict(session_info),
                "output": output
            }, ensure_ascii=False, indent=2)
            
        except CDBError as e:
            self.logger.error(f"CDB错误: {e}")
            return json.dumps({
                "success": False,
                "error": f"CDB错误: {str(e)}",
                "command": command,
                "session_id": getattr(e, 'session_id', None)
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"执行命令失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"执行失败: {str(e)}",
                "command": command
            }, ensure_ascii=False, indent=2)
    
    @kernel_function(description="Close and unload a crash dump session to free up resources")
    def close_windbg_dump(self, dump_path: str) -> str:
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
                }, ensure_ascii=False, indent=2)
            else:
                return json.dumps({
                    "success": False,
                    "message": f"未找到活跃的崩溃转储会话: {dump_path}",
                    "session_id": session_id
                }, ensure_ascii=False, indent=2)
                
        except Exception as e:
            self.logger.error(f"关闭崩溃转储会话失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"关闭失败: {str(e)}"
            }, ensure_ascii=False, indent=2)
    
    @kernel_function(description="Close a remote debugging connection and free up resources")
    def close_windbg_remote(self, connection_string: str) -> str:
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
                }, ensure_ascii=False, indent=2)
            else:
                return json.dumps({
                    "success": False,
                    "message": f"未找到活跃的远程连接: {connection_string}",
                    "session_id": session_id
                }, ensure_ascii=False, indent=2)
                
        except Exception as e:
            self.logger.error(f"关闭远程连接失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"关闭失败: {str(e)}"
            }, ensure_ascii=False, indent=2)
    
    @kernel_function(description="List Windows crash dump files in a specified directory")
    def list_windbg_dumps(
        self,
        directory_path: Optional[str] = None,
        recursive: bool = False
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
                    }, ensure_ascii=False, indent=2)
            
            if not os.path.exists(directory_path) or not os.path.isdir(directory_path):
                return json.dumps({
                    "success": False,
                    "error": f"目录未找到: {directory_path}"
                }, ensure_ascii=False, indent=2)
            
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
                }, ensure_ascii=False, indent=2)
            
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
            }, ensure_ascii=False, indent=2)
            
        except Exception as e:
            self.logger.error(f"列出转储文件失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"列出失败: {str(e)}"
            }, ensure_ascii=False, indent=2)
    
    @kernel_function(description="List all active CDB debugging sessions and their status")
    def list_windbg_sessions(self) -> str:
        """
        列出所有活跃的CDB调试会话及其状态
        
        Returns:
            str: JSON格式的会话列表
        """
        try:
            # 清理死会话
            self.session_manager.cleanup_dead_sessions()
            
            # 获取所有会话信息
            sessions_info = self.session_manager.list_sessions()
            
            sessions_data = []
            for session_info in sessions_info:
                sessions_data.append(asdict(session_info))
            
            self.logger.info(f"列出{len(sessions_data)}个活跃会话")
            
            return json.dumps({
                "success": True,
                "count": len(sessions_data),
                "sessions": sessions_data
            }, ensure_ascii=False, indent=2)
            
        except Exception as e:
            self.logger.error(f"列出会话失败: {e}")
            return json.dumps({
                "success": False,
                "error": f"列出失败: {str(e)}"
            }, ensure_ascii=False, indent=2)