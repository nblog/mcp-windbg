"""MCP WinDBG 测试脚本

用于验证重构后的功能是否正常工作
"""

import os
import sys
import logging
import tempfile
from pathlib import Path

# 添加src路径到Python路径
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

def test_imports():
    """测试模块导入"""
    print("测试模块导入...")
    
    try:
        from mcp_server_windbg.cdb_session import CDBSession, SessionManager, CDBError
        print("✅ CDB会话模块导入成功")
    except ImportError as e:
        print(f"❌ CDB会话模块导入失败: {e}")
        return False
    
    try:
        from mcp_server_windbg.windbg_plugin import WinDbgPlugin
        print("✅ WinDBG插件模块导入成功")
    except ImportError as e:
        print(f"❌ WinDBG插件模块导入失败: {e}")
        return False
    
    try:
        from mcp_server_windbg.main import create_kernel, parse_arguments
        print("✅ 主程序模块导入成功")
    except ImportError as e:
        print(f"❌ 主程序模块导入失败: {e}")
        return False
    
    return True

def test_session_manager():
    """测试会话管理器"""
    print("\\n测试会话管理器...")
    
    from mcp_server_windbg.cdb_session import SessionManager
    
    manager = SessionManager()
    
    # 测试列出空会话
    sessions = manager.list_sessions()
    assert len(sessions) == 0, "初始会话列表应为空"
    print("✅ 空会话列表测试通过")
    
    # 测试清理死会话
    manager.cleanup_dead_sessions()
    print("✅ 清理死会话测试通过")
    
    return True

def test_windbg_plugin():
    """测试WinDBG插件"""
    print("\\n测试WinDBG插件...")
    
    from mcp_server_windbg.windbg_plugin import WinDbgPlugin
    
    plugin = WinDbgPlugin()
    
    # 测试配置方法
    plugin.set_timeout(60)
    plugin.set_cdb_path("test_path")
    plugin.set_symbols_path("test_symbols")
    print("✅ 插件配置方法测试通过")
    
    # 测试列出会话（应为空）
    result = plugin.list_windbg_sessions()
    assert '"success": true' in result, "列出会话应该成功"
    assert '"count": 0' in result, "会话数量应为0"
    print("✅ 列出空会话测试通过")
    
    # 测试列出转储文件（使用临时目录）
    with tempfile.TemporaryDirectory() as temp_dir:
        result = plugin.list_windbg_dumps(directory_path=temp_dir)
        assert '"success": true' in result, "列出转储文件应该成功"
        print("✅ 列出转储文件测试通过")
    
    return True

def test_error_handling():
    """测试错误处理"""
    print("\\n测试错误处理...")
    
    from mcp_server_windbg.windbg_plugin import WinDbgPlugin
    
    plugin = WinDbgPlugin()
    
    # 测试无效的转储路径
    result = plugin.open_windbg_dump(dump_path="nonexistent_file.dmp")
    assert '"success": false' in result, "无效路径应该返回错误"
    print("✅ 无效转储路径错误处理测试通过")
    
    # 测试无效的命令参数
    result = plugin.run_windbg_cmd(command="test")  # 缺少路径参数
    assert '"success": false' in result, "缺少参数应该返回错误"
    print("✅ 无效命令参数错误处理测试通过")
    
    return True

def main():
    """主测试函数"""
    print("=== MCP WinDBG 重构验证测试 ===\\n")
    
    # 设置日志级别
    logging.basicConfig(level=logging.ERROR)  # 减少测试期间的日志输出
    
    tests = [
        test_imports,
        test_session_manager,
        test_windbg_plugin,
        test_error_handling,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ 测试 {test.__name__} 失败: {e}")
            failed += 1
    
    print(f"\\n=== 测试结果 ===")
    print(f"✅ 通过: {passed}")
    print(f"❌ 失败: {failed}")
    print(f"📊 总计: {passed + failed}")
    
    if failed == 0:
        print("\\n🎉 所有测试通过！重构验证成功！")
        return True
    else:
        print(f"\\n⚠️  有 {failed} 个测试失败，需要进一步检查。")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)