# Architecture

textish serves [Textual](https://textual.textualize.io/) TUI applications over SSH. Each SSH connection runs the configured app in its own **subinterpreter** (Python 3.14+), bridged to the SSH channel over cross-interpreter queues. One server process hosts many apps at once, with per-session module isolation and, thanks to the per-interpreter GIL, real multi-core parallelism.

## Directory Structure

```
textish/
├── src/textish/
│   ├── __init__.py       # Public API: serve()/serve_async(), AppConfig, authorized_keys()
│   ├── server.py         # SSH server layer (TextishSSHServer, session, SessionManager)
│   ├── config.py         # AppConfig dataclass with validation
│   ├── cli.py            # CLI entry point
│   └── subinterp/
│       ├── session.py    # SubinterpAppSession — main-interpreter side
│       ├── _worker.py    # QueueDriver + run_app — runs inside the subinterpreter
│       └── _demo_app.py  # Small apps for the example, tests, and benchmark
├── tests/
│   ├── unit/             # Mocked unit tests for the server layer
│   └── integration/      # Real SSH server + client end-to-end tests (3.14+)
├── examples/
│   ├── app.py            # Demo Wordle app (Textual)
│   └── main.py           # Minimal serve() usage
└── benchmarks/
    └── bench_subinterp.py  # Per-session memory and render-time benchmark
```

## Component Overview

### `__init__.py` — Public API

`serve(app, *, host, port, ...)` is the friendly, blocking entry point. It accepts a Textual `App` subclass, a zero-argument factory, or a `"module:attr"` string; derives the `module:qualname` import ref (rejecting apps defined in `__main__`, which a subinterpreter cannot import); forwards the running script's directory and cwd as `import_paths` so a local app is importable in the subinterpreter; generates a host key if needed; and runs the server (with uvloop if installed). `serve_async(config)` is the async entry for embedding in an existing event loop. `authorized_keys(path)` returns an auth callback backed by an OpenSSH `authorized_keys` file.

### `config.py` — AppConfig

Dataclass validated at construction. Holds host, port, `app_ref` (the app import path `package.module:attr`), host key path, connection limit, an optional auth callback, and `import_paths` (extra `sys.path` entries for the subinterpreter). Validation rejects an empty or malformed `app_ref` and a missing host key file.

### `server.py` — SSH Server Layer

**`SessionManager`** tracks all in-flight `SubinterpAppSession.run()` tasks. On shutdown it cancels them, so no subinterpreters are left running.

**`TextishSSHServer`** handles one TCP connection: enforces `max_connections`, advertises public-key auth when an auth callback is configured, and creates a raw-bytes channel (`encoding=None`) plus a session for each shell request.

**`TextishSSHServerSession`** bridges asyncssh protocol events to a `SubinterpAppSession`:

- `pty_requested` — stores terminal dimensions, approves the PTY.
- `session_started` — rejects non-PTY connections; otherwise creates the `SubinterpAppSession`, starts its run task, and starts the input consumer.
- `data_received` — enqueues raw bytes (a single consumer coroutine forwards them in FIFO order).
- `terminal_size_changed` — calls `SubinterpAppSession.resize()`.
- `eof_received` / `connection_lost` — cancel the run task, which tears down the subinterpreter.

### `subinterp/session.py` — SubinterpAppSession (main interpreter)

Creates two cross-interpreter queues and a subinterpreter, then runs the worker on its own OS thread via `Interpreter.call_in_thread`. It pumps the outbound queue to the SSH channel and forwards input, resize, and exit messages onto the inbound queue. On teardown it closes the channel, signals the worker to exit, joins the thread, and closes the interpreter. `SUBINTERP_AVAILABLE` reports whether the runtime is 3.14+.

### `subinterp/_worker.py` — QueueDriver + run_app (subinterpreter)

Runs inside each subinterpreter and stays pure Python — it must not import asyncssh, uvloop, or cryptography (those live in the main interpreter).

**`QueueDriver`** is a `textual.driver.Driver` that replaces the terminal assumptions: `write` puts `("D", bytes)` on the outbound queue; input bytes are fed in, decoded, parsed by Textual's `XTermParser`, and posted to the app as events; resize is an explicit call; lifecycle emits the alternate-screen / mouse / cursor sequences.

**`run_app`** is the subinterpreter entry point: it imports the app from `app_ref`, assigns `app.driver_class` to a queue-bound driver, runs `app.run_async(size=...)`, and runs an input-pump task that drains the inbound queue.

## Data Flow

### Connection lifecycle

```
TCP connect
  → TextishSSHServer.connection_made()   [enforce limit]
  → pty_requested()                       [store dimensions]
  → session_requested()                   [raw-bytes channel + session]
  → session_started()
      → create SubinterpAppSession
      → run task  →  registered with SessionManager
      → input consumer task

[RUNNING]
  main interpreter                         subinterpreter (own thread + GIL)
  keystrokes → data_received → in_queue ─► input pump → QueueDriver.feed → app
  channel.write ◄─ out pump ◄─ out_queue ◄─ QueueDriver.write ◄─ app render

[RESIZE]  terminal_size_changed → in_queue ("R") → QueueDriver.resize
[DISCONNECT] eof/connection_lost → cancel run task → SubinterpAppSession
             teardown: close channel, ("X") to worker, join thread, close interp
```

## Key Design Decisions

**Subinterpreter per connection.** Each session gets its own module state and, on 3.14+, its own GIL. Sessions therefore render in parallel across cores, and a Python-level failure in one is far less likely to disturb another. This is much cheaper than a subprocess (a few MB vs tens of MB) while giving stronger isolation than running every app in one shared interpreter.

**C libraries stay in the main interpreter.** asyncssh and cryptography are not guaranteed subinterpreter-safe, so only the pure-Python Textual app and the driver run in the subinterpreter. Bytes cross the boundary through queues.

**Bytes over queues, not shared objects.** Subinterpreters do not share arbitrary Python objects, so the SSH channel cannot be handed across. The driver serialises output to `("D", bytes)` messages; a main-interpreter pump forwards them to the channel. This adds one small copy per I/O.

**PTY required.** Textual relies on a real terminal to render. Non-PTY connections are rejected early with a clear message.

**FIFO input queue.** Client keystrokes are drained by a single consumer before being forwarded, so bytes reach the app in arrival order without locks.

**Graceful shutdown.** `SessionManager` cancels every run task before the server exits; each `SubinterpAppSession` tears down its subinterpreter in a `finally` block.

## Security

Subinterpreters are an isolation and parallelism feature, not a security boundary. The CPython documentation states they must not be used in security-sensitive situations: a malicious C extension can cross interpreters, and a hard crash still ends the whole process. Serve only trusted apps; sandbox untrusted code at the OS level (separate low-privilege user, cgroup limits, seccomp or landlock) instead.

## Technology Stack

| Layer         | Library                 | Version    |
| ------------- | ----------------------- | ---------- |
| SSH server    | asyncssh                | ≥2.24, <3 |
| TUI framework | textual                 | ≥8.2, <9  |
| Isolation     | concurrent.interpreters | stdlib (3.14+) |
| Async runtime | asyncio / uvloop        | —         |
| Language      | Python                  | ≥3.14     |

## Limitations

- **Python 3.14+ only** — the backend depends on `concurrent.interpreters`.
- **Unix only** — targets POSIX; Windows is not supported.
- **App must be importable** — referenced by `module:attr`, not a shell command.
- **Shared process** — a whole-process fault affects all sessions.
- **Subinterpreter-safe apps only** — the app and its dependencies must run under multiple interpreters (Textual and its tree are pure Python).
