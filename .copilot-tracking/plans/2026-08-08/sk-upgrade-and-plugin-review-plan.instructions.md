<!-- markdownlint-disable-file -->
# Plan: semantic-kernel upgrade + mcp_server_windbg review

## User Requests

1. Upgrade `semantic-kernel`, then review `src/mcp_server_windbg` for improvements.
2. Add `__doc__` anchors referencing <https://learn.microsoft.com/windows-hardware/drivers/debugger/cdb-command-line-options>.
3. Remove `env=` from settings fields that CDB already resolves through native env vars (`_NT_SYMBOL_PATH`, `_NT_SOURCE_PATH`); give `symbol_path` a default of `SRV*https://msdl.microsoft.com/download/symbols`; make all externally exposed CLI `description` text English to avoid terminal mojibake.
4. Beyond unit tests, validate the MCP end to end with `mcporter` against a real `%LOCALAPPDATA%\CrashDumps\*.dmp` file.

## Context Summary

- `semantic-kernel` latest is `1.44.1`; project pins `>=1.30.0`.
- `pydantic[dotenv]` is not a valid extra (uv emits a warning); `main.py` imports `dotenv` directly, so `python-dotenv` must be an explicit dependency.
- `Kernel.as_mcp_server(prompts=..., server_name=..., version=..., instructions=...)` signature is unchanged in 1.44.1 — no call-site migration needed.
- `Field(env=...)` is deprecated in Pydantic v2 (removal in v3); pydantic-settings already derives `CDB_PATH` from the field name `cdb_path`.

## Confirmed Defects

| ID | File | Issue |
|----|------|-------|
| D1 | `windbg_plugin.py` | Registry subkey uses `r"...\\Microsoft\\..."`, so the raw string contains doubled backslashes and `LocalDumps\DumpFolder` lookup always fails |
| D2 | `cdb_session.py` | `_get_system_encoding()` falls off the end and returns `None` on non-Windows |
| D3 | `windbg_plugin.py` | `open_windbg_remote` uses `Field(...)` as a plain-function default instead of `Annotated` metadata |
| D4 | `windbg_plugin.py` | `open_windbg_dump(dump_path: str)` is required, so the dump-discovery branch is unreachable |
| D5 | `main.py` | No shutdown hook; CDB child processes leak when the server exits |
| D6 | `windbg_plugin.py` | `run_windbg_cmd` forwards arbitrary commands; CDB `.shell` allows host command execution |
| D7 | `cdb_session.py` | Unused imports (`sys`, `Any`) and unused `PROMPT_REGEX`; f-string with no placeholder |
| D8 | `pyproject.toml` | Invalid `pydantic[dotenv]` extra |

## Implementation Checklist

### Phase 1: Dependencies

<!-- parallelizable: false -->

- [x] Pin `semantic-kernel[mcp]>=1.44.1`, replace `pydantic[dotenv]` with `pydantic>=2.9` + `python-dotenv>=1.0`

### Phase 2: cdb_session.py

<!-- parallelizable: false -->

- [x] Fix D2, D7; add CDB command-line-options doc anchors to module and `_start_cdb_process` docstrings
- [x] Harden CDB launch with `-noshell` (D6) and widen CDB discovery to ARM64/`PATH`

### Phase 3: windbg_plugin.py

<!-- parallelizable: false -->

- [x] Fix D1, D3, D4; drop `env=` from `symbol_path`/`source_path` and default `symbol_path` to the MS symbol server
- [x] Convert kernel_function descriptions/params to English `Annotated` metadata with doc anchors

### Phase 4: main.py

<!-- parallelizable: false -->

- [x] English argparse text, `atexit` session shutdown (D5), doc anchor on `--symbol-path`/`--source-path`

### Phase 5: Validation

<!-- parallelizable: false -->

- [x] `test_refactor.py` passes
- [x] `mcporter list` + `mcporter call` against a real dump from `%LOCALAPPDATA%\CrashDumps`

## Success Criteria

- Latest semantic-kernel installs cleanly with no invalid-extra warning.
- No Pydantic deprecation warnings from plugin config.
- `_get_local_dumps_path()` resolves the registry `DumpFolder` when configured.
- All externally visible CLI/tool descriptions are English.
- `mcporter` successfully lists tools and analyzes a real dump end to end.
