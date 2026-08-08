<!-- markdownlint-disable-file -->
# Plan: follow-up items 1-4

## User Requests

1. Replace `test_refactor.py` with a pytest suite that locks in the eight earlier fixes.
2. Add a session idle-reaper using the already-tracked `SessionInfo.last_activity`.
3. Make `!analyze -v` timeout-aware for cold symbol caches.
4. Update `README.md` and `README_EN.md` for the new symbol-path/env/`-noshell` behavior.

## Context Summary

- Measured: cold `!analyze -v` = 94.9s, warm = ~9s. Symbols land in `C:\ProgramData\Dbg\sym` (428 files) via the debugger's default downstream store, so no explicit cache needs configuring.
- mcporter's default call timeout is 60s, below the cold-cache cost. Client timeouts are not controllable server-side, so the server must return *something* useful within a bounded window.
- `send_command` currently sets `state = ERROR` on timeout. `is_alive()` then returns False, so the next `create_session` tears down CDB and reloads the dump — discarding symbol work already paid for. This is the real cold-cache pain.
- `COMMAND_MARKER` is a single fixed string. A late marker from a timed-out command can satisfy the *next* command's wait, cross-contaminating output. Sequenced markers fix this and make resync possible.
- `SessionManager.cleanup_dead_sessions()` only runs when `list_windbg_sessions` is called; idle sessions hold a CDB process indefinitely.
- No pytest in the environment yet; needs a dev dependency group.

## Design Decisions

- **Sequenced command markers**: `.echo COMMAND_COMPLETED_MARKER_<n>`. The reader publishes output only when the captured id matches the expected id, discarding stale batches.
- **Non-destructive timeouts**: on timeout, attempt `_resynchronize()` (fresh marker, short grace). Success returns the session to `READY`, preserving the loaded dump and cached symbols. Raise `CDBTimeoutError(CDBError)` so callers can distinguish timeout from failure.
- **Partial analysis results**: `open_windbg_dump` gains `analysis_timeout`. When `!analyze -v` exceeds it, return the sections that completed with `partial: true` and a retry hint, instead of failing the whole call.
- **Idle reaper**: `SessionManager(idle_timeout=...)` with an opt-in daemon thread; reap when `now - last_activity > idle_timeout`.

## Implementation Checklist

### Phase 1: cdb_session.py — sequenced markers and resync

<!-- parallelizable: false -->

- [x] Replace the fixed marker with a sequenced marker and id-matched publishing
- [x] Add `CDBTimeoutError`, `_resynchronize()`, non-destructive timeout handling in `send_command`

### Phase 2: cdb_session.py — idle reaper

<!-- parallelizable: false -->

- [x] Add `idle_timeout` + reaper thread to `SessionManager`; reap on `last_activity`

### Phase 3: windbg_plugin.py — partial analysis

<!-- parallelizable: false -->

- [x] Add `analysis_timeout` to `open_windbg_dump`, return partial results on timeout
- [x] Surface `CDBTimeoutError` distinctly in `run_windbg_cmd`

### Phase 4: pytest suite

<!-- parallelizable: false -->

- [x] Add dev dependency group + pytest config
- [x] `tests/test_config.py`, `tests/test_plugin.py`, `tests/test_cdb_session.py`, `tests/test_kernel_contract.py`
- [x] Delete `test_refactor.py`

### Phase 5: READMEs

<!-- parallelizable: false -->

- [x] Update both READMEs: symbol path default, env var behavior, `-noshell`, idle reaper, cold-cache guidance

### Phase 6: Validation

<!-- parallelizable: false -->

- [x] pytest green, no deprecation warnings
- [x] mcporter end-to-end against a real dump, including a forced cold-cache partial result

## Success Criteria

- pytest covers each of the eight earlier fixes and the new behavior; suite runs without a real dump.
- A timed-out command no longer destroys a healthy session.
- `open_windbg_dump` returns usable output within a bounded window on a cold cache.
- READMEs match actual configuration behavior.
