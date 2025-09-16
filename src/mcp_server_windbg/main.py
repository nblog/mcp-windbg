"""MCP WinDBG 服务器主程序

基于Semantic Kernel框架的WinDBG/CDB调试工具MCP服务器实现
"""

import argparse
import logging
from typing import Any, Literal

from semantic_kernel import Kernel

from .windbg_plugin import WinDbgPlugin

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


def create_kernel() -> Kernel:
    """创建Semantic Kernel"""
    kernel = Kernel()
    
    # 添加WinDBG插件
    kernel.add_plugin(WinDbgPlugin(), plugin_name="windbg")
    
    logger.info("Kernel初始化完成，已加载WinDBG调试功能模块")
    return kernel


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="运行MCP服务器，支持SSE和STDIO传输方式")
    parser.add_argument(
        "--transport",
        type=str,
        choices=["sse", "stdio"],
        default="stdio",
        help="传输方式 (默认: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3001,
        help="SSE传输端口 (SSE模式必需)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志级别 (默认: INFO)",
    )
    parser.add_argument(
        "--cdb-path",
        type=str,
        help="自定义CDB.exe路径",
    )
    parser.add_argument(
        "--symbols-path",
        type=str,
        help="自定义符号路径",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="命令超时时间（秒，默认: 600）",
    )
    return parser.parse_args()


def run(
    transport: Literal["sse", "stdio"] = "stdio", 
    port: int | None = None,
    cdb_path: str | None = None,
    symbols_path: str | None = None,
    timeout: int = 600
) -> None:
    """
    异步运行 MCP WinDBG 服务器
    
    Args:
        transport: 传输协议，支持 "sse" 或 "stdio"
        port: SSE 服务器端口（仅在 transport="sse" 时使用）
        cdb_path: 自定义CDB.exe路径
        symbols_path: 自定义符号路径
        timeout: 命令超时时间
    """
    try:
        from dotenv import load_dotenv
        
        # 加载环境变量
        load_dotenv()
        
        # 验证环境
        if not validate_environment():
            logger.error("环境验证失败")
            return
        
        # 创建Kernel
        kernel = create_kernel()
        
        # 设置插件配置
        windbg_plugin = kernel.plugins["windbg"]
        if cdb_path:
            windbg_plugin.set_cdb_path(cdb_path)
        if symbols_path:
            windbg_plugin.set_symbols_path(symbols_path)
        windbg_plugin.set_timeout(timeout)
        
        # 创建MCP服务器
        server = kernel.as_mcp_server(
            version="0.1.0",
            server_name="mcp-server-windbg",
            instructions=(
                "Windows调试工具MCP服务器。提供CDB/WinDBG调试会话管理、"
                "崩溃转储分析、远程调试连接和调试命令执行功能。"
                "支持自动分析崩溃转储并提供详细的调试信息。"
            )
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
        symbols_path=args.symbols_path,
        timeout=args.timeout
    )


if __name__ == "__main__":
    main()