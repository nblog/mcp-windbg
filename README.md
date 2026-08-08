# MCP WinDBG - Windows调试工具MCP服务器

基于Semantic Kernel框架的Windows调试工具Model Context Protocol (MCP) 服务器，提供CDB/WinDBG调试会话管理、崩溃转储分析和远程调试功能。

## 🚀 功能特性

### 核心功能
- **崩溃转储分析**: 使用CDB/WinDBG分析Windows崩溃转储文件
- **远程调试**: 连接到远程调试会话进行实时分析
- **会话管理**: 智能的CDB会话生命周期管理
- **命令执行**: 执行任意WinDBG/CDB调试命令
- **进度跟踪**: 详细的日志记录和进度显示

### 架构特点
- **Semantic Kernel框架**: 基于Microsoft Semantic Kernel实现的MCP服务
- **增强的日志系统**: 使用`logging`模块提供详细的进度信息
- **会话复用**: 智能的会话管理，避免重复创建相同的调试会话
- **错误处理**: 完善的错误处理和超时控制机制
- **线程安全**: 多线程安全的会话管理

## 🛠️ 使用方法

### 启动 MCP 服务器

#### 快速启动 (STDIO 模式)

```json
{
  "mcpServers": {
    "mcp-server-windbg": {
      "command": "uvx",
      "args": [
        "--from",
        "mcp-server-windbg@git+https://github.com/nblog/mcp-windbg.git",
        "mcp-server-windbg"
      ]
    }
  }
}
```

### 可用的 MCP 工具

服务提供以下 MCP 工具函数：

#### 1. `open_windbg_dump`
分析Windows崩溃转储文件
```json
{
  "dump_path": "C:\\path\\to\\crash.dmp",
  "include_stack_trace": true,
  "include_modules": true,
  "include_threads": true
}
```

#### 2. `open_windbg_remote`
连接到远程调试会话
```json
{
  "connection_string": "tcp:Port=5005,Server=192.168.0.100",
  "include_stack_trace": false,
  "include_modules": false,
  "include_threads": false
}
```

> `open_windbg_dump` 支持 `analysis_timeout`（秒）。冷符号缓存下首次 `!analyze -v`
> 可能耗时较长，超时后返回带 `"partial": true` 的部分结果与提示，会话与已下载的符号保持可用。

#### 3. `run_windbg_cmd`
执行特定的WinDBG命令
```json
{
  "command": "!analyze -v",
  "dump_path": "C:\\path\\to\\crash.dmp"
}
```

#### 4. `list_windbg_dumps`
列出目录中的转储文件
```json
{
  "directory_path": "C:\\CrashDumps",
  "recursive": true
}
```

#### 5. `list_windbg_sessions`
列出所有活跃的调试会话
```json
{}
```

#### 6. `close_windbg_dump`
关闭崩溃转储会话
```json
{
  "dump_path": "C:\\path\\to\\crash.dmp"
}
```

#### 7. `close_windbg_remote`
关闭远程调试连接
```json
{
  "connection_string": "tcp:Port=5005,Server=192.168.0.100"
}
```

## 📋 配置选项

### 命令行参数
- `--transport`: 传输方式（`stdio` 或 `sse`，默认：stdio）
- `--port`: SSE模式端口（默认：3001）
- `--log-level`: 日志级别（DEBUG, INFO, WARNING, ERROR）
- `--cdb-path`: 自定义CDB.exe路径
- `--symbol-path`: 符号路径，对应CDB的 `-y`
- `--source-path`: 源代码路径，对应CDB的 `-srcpath`
- `--timeout`: 命令超时时间（秒，默认：600）
- `--idle-timeout`: 空闲会话回收阈值（秒，默认：1800，`0` 禁用）

### 符号路径

`--symbol-path` 缺省为 Microsoft 公共符号服务器，开箱即可解析系统模块符号：

```text
SRV*https://msdl.microsoft.com/download/symbols
```

传入空字符串则跳过 `-y`，让 CDB 回落到自身的 `_NT_SYMBOL_PATH`：

```bash
mcp-server-windbg --symbol-path ""
```

### 环境变量

符号与源路径**不读取**裸名 `SYMBOL_PATH` / `SOURCE_PATH`。CDB 自身已经识别
[`_NT_SYMBOL_PATH`](https://learn.microsoft.com/windows-hardware/drivers/debugger/symbol-path)
与 `_NT_SOURCE_PATH`，再占用一套裸名变量会与调试器原生行为产生歧义。
若需通过环境变量覆盖，请使用带 `MCP_WINDBG_` 前缀的命名空间变量。

可以创建`.env`文件来设置默认配置：

```env
CDB_PATH=C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe
DEFAULT_TIMEOUT=600
MCP_WINDBG_SYMBOL_PATH=srv*C:\ProgramData\Dbg\sym*https://msdl.microsoft.com/download/symbols
MCP_WINDBG_SOURCE_PATH=C:\Users\qt\work\qt
```

命令行参数优先级高于环境变量。

### 安全性

调试会话以 [`-noshell`](https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options)
启动，`.shell` 被调试器拒绝，因此 `run_windbg_cmd` 无法借由调试命令在宿主机上执行程序。

需要注意 `run_windbg_cmd` 仍可打开服务进程有权读取的任意转储文件路径，
请仅在受信任的调用方之间暴露该服务。

## 🏗️ 架构设计

### 项目结构
```
src/mcp_server_windbg/
├── __init__.py          # 包初始化
├── main.py              # 服务器主程序
├── windbg_plugin.py     # WinDBG插件实现
└── cdb_session.py       # CDB会话管理
```

## 📚 技术细节

### CDB会话生命周期
1. **初始化**: 创建CDB进程，建立输入/输出管道
2. **就绪**: 等待CDB初始化完成，发送测试命令
3. **执行**: 接收并执行调试命令，跟踪进度
4. **清理**: 优雅关闭CDB进程，释放资源

### 命令执行机制
- 使用命令标记（`COMMAND_COMPLETED_MARKER`）检测命令完成
- 多线程输出读取，避免阻塞
- 超时控制和错误恢复

### 会话管理策略
- 基于文件路径或连接字符串的会话标识
- 会话复用避免重复创建
- 后台线程回收死会话与空闲超时会话（每个会话持有一个CDB进程及其转储映射）
- 命令超时后尝试重新同步：调试器健康时保留会话，避免重新加载转储和重新下载符号

## 🧪 测试

```bash
uv pip install -e . pytest
pytest                        # 全部测试
pytest -m "not requires_cdb"  # 跳过需要真实调试器与转储文件的测试
```

## 🔍 故障排除

### 常见问题

**Q: 找不到CDB.exe**
A: 确保已安装 [Windows Driver Kit](https://learn.microsoft.com/windows-hardware/drivers/download-the-wdk)，或使用`--cdb-path`指定路径

**Q: 命令执行超时**
A: 增加超时时间使用`--timeout`参数，或检查CDB是否响应

**Q: 首次分析很慢，MCP客户端提前超时**
A: 冷符号缓存下首次 `!analyze -v` 需要下载符号，可能耗时超过一分钟，
而多数MCP客户端的默认调用超时更短。此时 `open_windbg_dump` 会返回带
`"partial": true` 的部分结果；符号下载完成后重新调用即可显著加快。
也可以调大客户端的调用超时，或用 `analysis_timeout` 调整服务端上限

**Q: 远程连接失败**
A: 检查网络连接和防火墙设置，确认目标机器已启动调试服务器
