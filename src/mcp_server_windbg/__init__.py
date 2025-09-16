"""MCP WinDBG Server - Windows调试工具的模型上下文协议服务器

基于Semantic Kernel框架实现的Windows调试工具MCP服务器。
提供CDB/WinDBG调试会话管理、崩溃转储分析和远程调试功能。
"""

from .main import main

__all__ = ["main"]