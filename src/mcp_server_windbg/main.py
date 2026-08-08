"""MCP WinDBG 服务器主程序

基于Semantic Kernel框架的WinDBG/CDB调试工具MCP服务器实现
"""

import argparse
import atexit
import logging
from typing import Any, Literal

from semantic_kernel import Kernel
from pydantic_settings import BaseSettings

from .cdb_session import DEFAULT_IDLE_TIMEOUT, session_manager
from .windbg_plugin import WinDbgPlugin, WinDbgPluginConfig
from .prompts import get_all_prompts

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def validate_environment():
    """验证环境配置"""
    import platform
    if platform.system() != "Windows":
        logger.warning("MCP WinDBG服务器主要设计用于Windows系统")
        return False
    return True


def create_kernel(config: BaseSettings | None = None) -> Kernel:
    """创建Semantic Kernel"""
    kernel = Kernel()
    
    # 添加WinDBG插件
    kernel.add_plugin(WinDbgPlugin(config), plugin_name="windbg")
    
    logger.info("Kernel初始化完成，已加载WinDBG调试功能模块")
    return kernel


def parse_arguments():
    """
    解析命令行参数
    
    帮助文本使用英文，避免在非UTF-8控制台代码页下出现乱码。
    符号/源路径选项对应CDB的 ``-y`` / ``-srcpath``：
    https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options
    """
    parser = argparse.ArgumentParser(
        description="Run the WinDBG MCP server over STDIO or SSE transport."
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["sse", "stdio"],
        default="stdio",
        help="Transport to serve on (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3001,
        help="Port for SSE transport (required in SSE mode)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--cdb-path",
        type=str,
        help="Path to cdb.exe (auto-detected when omitted)",
    )
    parser.add_argument(
        "--symbol-path",
        type=str,
        help=(
            "Symbol search path passed to CDB via -y. "
            "Defaults to the Microsoft public symbol server; "
            "pass an empty string to fall back to _NT_SYMBOL_PATH."
        ),
    )
    parser.add_argument(
        "--source-path",
        type=str,
        help="Source search path passed to CDB via -srcpath (falls back to _NT_SOURCE_PATH)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Command timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=DEFAULT_IDLE_TIMEOUT,
        help=(
            "Reclaim debugging sessions idle for this many seconds "
            f"(default: {DEFAULT_IDLE_TIMEOUT}, 0 disables reclamation)"
        ),
    )
    return parser.parse_args()


def run(
    transport: Literal["sse", "stdio"] = "stdio", 
    port: int | None = None,
    cdb_path: str | None = None,
    symbol_path: str | None = None,
    source_path: str | None = None,
    timeout: int = 600,
    idle_timeout: int = DEFAULT_IDLE_TIMEOUT
) -> None:
    """
    异步运行 MCP WinDBG 服务器
    
    Args:
        transport: 传输协议，支持 "sse" 或 "stdio"
        port: SSE 服务器端口（仅在 transport="sse" 时使用）
        cdb_path: 自定义CDB.exe路径
        symbol_path: 自定义符号路径
        source_path: 自定义源代码路径
        timeout: 命令超时时间
        idle_timeout: 空闲会话回收阈值，0表示禁用
    """
    try:
        from dotenv import load_dotenv
        
        # 加载环境变量
        load_dotenv()
        
        # 验证环境
        if not validate_environment():
            logger.error("环境验证失败")
            return
        
        overrides: dict[str, Any] = {"timeout": timeout}
        if cdb_path is not None:
            overrides["cdb_path"] = cdb_path
        if symbol_path is not None:
            overrides["symbol_path"] = symbol_path
        if source_path is not None:
            overrides["source_path"] = source_path
        windbg_config = WinDbgPluginConfig(**overrides)
        
        session_manager.idle_timeout = idle_timeout
        
        # 进程退出时释放遗留的CDB子进程
        atexit.register(session_manager.shutdown_all)
        
        # 创建Kernel
        kernel = create_kernel(windbg_config)
        
        # 创建MCP服务器
        from mcp_server_windbg import __version__ as version
        server = kernel.as_mcp_server(
            version=version,
            server_name="mcp-server-windbg",
            instructions=(
                "Windows debugging MCP server backed by CDB/WinDBG. Provides crash dump "
                "analysis, remote debugging connections, debugger command execution, and "
                "session lifecycle management."
            ),
            prompts=get_all_prompts()  # 添加内置提示词
        )
        
        # 启动服务器
        if transport == "sse" and port is not None:
            logger.info(f"启动SSE服务器，端口: {port}")
            
            import uvicorn
            from mcp.server.sse import SseServerTransport
            from starlette.applications import Starlette
            from starlette.routing import Mount, Route

            sse = SseServerTransport("/messages/")

            async def handle_sse(request):
                async with sse.connect_sse(
                    request.scope, request.receive, request._send
                ) as (read_stream, write_stream):
                    await server.run(
                        read_stream, write_stream, 
                        server.create_initialization_options()
                    )

            starlette_app = Starlette(
                debug=True,
                routes=[
                    Route("/sse", endpoint=handle_sse),
                    Mount("/messages/", app=sse.handle_post_message),
                ],
            )

            uvicorn.run(starlette_app, host="0.0.0.0", port=port)  # nosec
        elif transport == "stdio":
            logger.info("启动STDIO服务器")
            
            import anyio
            from mcp.server.stdio import stdio_server

            async def handle_stdin(stdin: Any | None = None, stdout: Any | None = None) -> None:
                async with stdio_server() as (read_stream, write_stream):
                    await server.run(
                        read_stream, write_stream, 
                        server.create_initialization_options()
                    )

            anyio.run(handle_stdin)
        
        else:
            raise ValueError("SSE模式需要指定端口号")
            
    except Exception as e:
        logger.error(f"服务器启动失败: {e}")
        raise


def main():
    """
    MCP WinDBG 服务器入口函数
    
    解析命令行参数并启动相应的 MCP 服务器
    """
    args = parse_arguments()
    
    # 设置日志级别
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    run(
        transport=args.transport, 
        port=args.port,
        cdb_path=args.cdb_path,
        symbol_path=args.symbol_path,
        source_path=args.source_path,
        timeout=args.timeout,
        idle_timeout=args.idle_timeout
    )


if __name__ == "__main__":
    main()