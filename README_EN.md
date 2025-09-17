# MCP WinDBG - Windows Debugging Tool MCP Server

A Model Context Protocol (MCP) server for Windows debugging tools based on the Semantic Kernel framework, providing CDB/WinDBG debugging session management, crash dump analysis, and remote debugging capabilities.

## 🚀 Features

### Core Features
- **Crash Dump Analysis**: Analyze Windows crash dump files using CDB/WinDBG
- **Remote Debugging**: Connect to remote debugging sessions for real-time analysis
- **Session Management**: Intelligent CDB session lifecycle management
- **Command Execution**: Execute arbitrary WinDBG/CDB debugging commands
- **Progress Tracking**: Detailed logging and progress display

### Architectural Features
- **Semantic Kernel Framework**: MCP service implemented based on Microsoft Semantic Kernel
- **Enhanced Logging System**: Provides detailed progress information using the `logging` module
- **Session Reuse**: Intelligent session management to avoid repeatedly creating the same debugging session
- **Error Handling**: Comprehensive error handling and timeout control mechanisms
- **Thread Safety**: Thread-safe session management

## 🛠️ Usage

### Starting the MCP Server

#### Quick Start (STDIO Mode)

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

### Available MCP Tools

The service provides the following MCP tool functions:

#### 1. `open_windbg_dump`
Analyze Windows crash dump files
```json
{
  "dump_path": "C:\\path\\to\\crash.dmp",
  "include_stack_trace": true,
  "include_modules": true,
  "include_threads": true
}
```

#### 2. `open_windbg_remote`
Connect to remote debugging sessions
```json
{
  "connection_string": "tcp:Port=5005,Server=192.168.0.100",
  "include_stack_trace": false,
  "include_modules": false,
  "include_threads": false
}
```

#### 3. `run_windbg_cmd`
Execute specific WinDBG commands
```json
{
  "command": "!analyze -v",
  "dump_path": "C:\\path\\to\\crash.dmp"
}
```

#### 4. `list_windbg_dumps`
List dump files in a directory
```json
{
  "directory_path": "C:\\CrashDumps",
  "recursive": true
}
```

#### 5. `list_windbg_sessions`
List all active debugging sessions
```json
{}
```

#### 6. `close_windbg_dump`
Close crash dump sessions
```json
{
  "dump_path": "C:\\path\\to\\crash.dmp"
}
```

#### 7. `close_windbg_remote`
Close remote debugging connections
```json
{
  "connection_string": "tcp:Port=5005,Server=192.168.0.100"
}
```

## 📋 Configuration Options

### Command Line Parameters
- `--transport`: Transport method (`stdio` or `sse`, default: stdio)
- `--port`: SSE mode port (default: 3001)
- `--log-level`: Log level (DEBUG, INFO, WARNING, ERROR)
- `--cdb-path`: Custom CDB.exe path
- `--symbols-path`: Custom symbols path
- `--timeout`: Command timeout in seconds (default: 600)

### Environment Variables
You can create a `.env` file to set default configurations:
```env
CDB_PATH=C:\\Program Files (x86)\\Windows Kits\\10\\Debuggers\\x64\\cdb.exe
SYMBOLS_PATH=srv*c:\\symbols*https://msdl.microsoft.com/download/symbols
DEFAULT_TIMEOUT=60
```

## 🏗️ Architecture Design

### Project Structure
```
src/mcp_server_windbg/
├── __init__.py          # Package initialization
├── main.py              # Server main program
├── windbg_plugin.py     # WinDBG plugin implementation
└── cdb_session.py       # CDB session management
```

## 📚 Technical Details

### CDB Session Lifecycle
1. **Initialization**: Create CDB process, establish input/output pipes
2. **Ready**: Wait for CDB initialization to complete, send test commands
3. **Execution**: Receive and execute debugging commands, track progress
4. **Cleanup**: Gracefully close CDB process, release resources

### Command Execution Mechanism
- Use command markers (`COMMAND_COMPLETED_MARKER`) to detect command completion
- Multi-threaded output reading to avoid blocking
- Timeout control and error recovery

### Session Management Strategy
- Session identification based on file path or connection string
- Session reuse to avoid duplicate creation
- Regular cleanup of dead sessions

## 🔍 Troubleshooting

### Common Issues

**Q: CDB.exe not found**
A: Ensure Windows SDK or debugging tools are installed, or specify the path using `--cdb-path`

**Q: Symbol loading failed**
A: Set the correct symbols path using `--symbols-path` or environment variables

**Q: Command execution timeout**
A: Increase timeout using the `--timeout` parameter, or check if CDB is responding

**Q: Remote connection failed**
A: Check network connection and firewall settings, confirm the target machine has started the debugging server
