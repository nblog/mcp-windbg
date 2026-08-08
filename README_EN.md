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

> `open_windbg_dump` accepts `analysis_timeout` in seconds. On a cold symbol cache the
> first `!analyze -v` can be slow; when it exceeds the limit the call returns partial
> results with `"partial": true` and a hint, keeping the session and downloaded symbols usable.

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
- `--symbol-path`: Symbol search path, passed to CDB as `-y`
- `--source-path`: Source search path, passed to CDB as `-srcpath`
- `--timeout`: Command timeout in seconds (default: 600)
- `--idle-timeout`: Reclaim sessions idle this long, in seconds (default: 1800, `0` disables)

### Symbol Path

`--symbol-path` defaults to the Microsoft public symbol server, so system module
symbols resolve out of the box:

```text
SRV*https://msdl.microsoft.com/download/symbols
```

Pass an empty string to skip `-y` and let CDB fall back to its own `_NT_SYMBOL_PATH`:

```bash
mcp-server-windbg --symbol-path ""
```

### Environment Variables

Symbol and source paths **do not** read the bare `SYMBOL_PATH` / `SOURCE_PATH`
variables. CDB already honors
[`_NT_SYMBOL_PATH`](https://learn.microsoft.com/windows-hardware/drivers/debugger/symbol-path)
and `_NT_SOURCE_PATH`, so claiming a second set of bare names would be ambiguous
against the debugger's native behavior. Use the `MCP_WINDBG_` prefixed variables
to override them through the environment.

You can create a `.env` file to set default configurations:

```env
CDB_PATH=C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe
DEFAULT_TIMEOUT=600
MCP_WINDBG_SYMBOL_PATH=srv*C:\ProgramData\Dbg\sym*https://msdl.microsoft.com/download/symbols
MCP_WINDBG_SOURCE_PATH=C:\Users\qt\work\qt
```

Command line arguments take precedence over environment variables.

### Security

Debugging sessions launch with
[`-noshell`](https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options),
so the debugger rejects `.shell` and `run_windbg_cmd` cannot execute host programs
through debugger commands.

Note that `run_windbg_cmd` can still open any dump path the server process is able
to read, so only expose this server to trusted callers.

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
- Background thread reclaims dead and idle sessions, since each session holds a CDB
  process and its dump mapping
- After a command timeout the session resynchronizes when the debugger is still
  healthy, preserving the loaded dump and already-downloaded symbols

## 🧪 Testing

```bash
uv pip install -e . pytest
pytest                        # full suite
pytest -m "not requires_cdb"  # skip tests needing a real debugger and dump file
```

## 🔍 Troubleshooting

### Common Issues

**Q: CDB.exe not found**
A: Ensure [Windows Driver Kit](https://learn.microsoft.com/windows-hardware/drivers/download-the-wdk) or debugging tools are installed, or specify the path using `--cdb-path`

**Q: Command execution timeout**
A: Increase timeout using the `--timeout` parameter, or check if CDB is responding

**Q: The first analysis is slow and my MCP client times out**
A: On a cold symbol cache the first `!analyze -v` downloads symbols and can take over
a minute, which is longer than most MCP clients' default call timeout. In that case
`open_windbg_dump` returns partial results with `"partial": true`; retrying after the
symbols are cached is substantially faster. You can also raise the client's call
timeout or tune the server-side limit with `analysis_timeout`

**Q: Remote connection failed**
A: Check network connection and firewall settings, confirm the target machine has started the debugging server
