# Architecture

textish serves [Textual](https://textual.textualize.io/) TUI applications over SSH. Each SSH connection gets its own isolated subprocess running the configured app, bridged via a pseudo-terminal (PTY).Syntax error in graph

## Directory Structure

```
textish/
├── src/textish/
│   ├── __init__.py       # Public API: serve(), authorized_keys()
│   ├── server.py         # SSH server layer (TextishSSHServer, TextishSSHServerSession, SessionManager)
│   ├── app_session.py    # Per-connection subprocess + PTY management
│   ├── config.py         # AppConfig dataclass with validation
│   ├── cli.py            # CLI entry point
│   └── types.py          # ProcessState enum
├── tests/
│   ├── unit/             # Mocked unit tests for server and app session
│   └── integration/      # Real SSH server + client end-to-end tests
└── examples/
    ├── app.py            # Demo Wordle app (Textual)
    └── main.py           # Minimal serve() usage
```

## Component Overview

### `config.py` — AppConfig

Dataclass validated at construction time. Holds all server parameters: host, port, app command, host key path, connection limit, environment variables, and an optional auth callback (sync or async).

### `server.py` — SSH Server Layer

Three classes:

**`SessionManager`** tracks all in-flight `AppSession.run()` tasks. On shutdown it cancels them all, ensuring no orphaned subprocesses.

**`TextishSSHServer`** handles one TCP connection. It enforces `max_connections`, advertises public-key auth if an auth callback is configured, and creates `(channel, session)` pairs for incoming shell requests.

**`TextishSSHServerSession`** handles one SSH shell session. It bridges asyncssh protocol events to `AppSession`:

- `pty_requested` — stores terminal dimensions, approves the PTY
- `session_started` — creates the `AppSession`, starts `AppSession.run()`, starts the input consumer task
- `data_received` — enqueues raw bytes into `_input_queue`
- `terminal_size_changed` — calls `AppSession.resize()`
- `eof_received` / `connection_lost` — cancels the run task

Input is serialized through a single consumer coroutine reading from `_input_queue` to guarantee FIFO ordering. A `None` sentinel drains and stops it on disconnect.

### `app_session.py` — AppSession

Manages the lifecycle of one subprocess + PTY pair for one SSH client.

**State machine:** `PENDING → RUNNING → STOPPING → STOPPED`

`run()` is the main coroutine:

1. Opens a PTY master/slave pair via `pty.openpty()`
2. Spawns the app command as a subprocess attached to the PTY slave, then closes the slave FD in the parent
3. Sets non-blocking I/O on the master FD; registers it with the asyncio event loop
4. Forwards PTY output → SSH channel in a read loop
5. On exit (normal or cancelled): closes the channel, then sends SIGTERM (escalating to SIGKILL after 3 s)

`send_input(data)` writes raw bytes from the SSH client to the PTY master (handles partial writes and OS errors if the subprocess has already exited).

`resize(cols, rows)` issues a `TIOCSWINSZ` ioctl so the subprocess receives `SIGWINCH` and reflows its layout.

### `__init__.py` — Public API

`serve(config)` is the async entry point. It creates the `SessionManager` and shared `active_connections` set, starts `asyncssh.create_server()`, and runs `serve_forever()`.

`authorized_keys(path)` returns an async auth callable that re-reads an OpenSSH `authorized_keys` file on every authentication attempt and matches by key blob.

## Data Flow

### Startup

```
main() / serve()
  → AppConfig (validate)
  → create SessionManager + active_connections set
  → asyncssh.create_server(TextishSSHServer factory)
  → server.serve_forever()
```

### Connection Lifecycle

```
TCP connect
  → TextishSSHServer.connection_made()  [enforce limit]
  → pty_requested()                     [store dimensions]
  → shell_requested()
  → session_started()
      → create AppSession
      → spawn AppSession.run() task  →  registered with SessionManager
      → start input consumer task

[RUNNING — bidirectional forwarding]
  SSH client keystrokes → data_received() → input_queue → AppSession.send_input() → PTY master
  PTY master output → AppSession.run() loop → channel.write() → SSH client

[RESIZE]
  terminal_size_changed() → AppSession.resize() → ioctl(TIOCSWINSZ) → subprocess SIGWINCH

[DISCONNECT]
  eof_received() or connection_lost()
    → cancel run task
    → AppSession finally: close channel → SIGTERM → [SIGKILL after 3 s]
    → SessionManager removes task
    → TextishSSHServer.connection_lost() removes from active_connections
```

### Subprocess I/O Detail

```
SSH Client                         Subprocess (Textual app)
   │                                       │
   │  keystrokes                           │
   ▼                                       │
data_received()                            │
   → input_queue                           │
   → input_consumer                        │
   → AppSession.send_input()               │
   → os.write(master_fd) ─────────────► PTY slave stdin
                                           │
PTY slave stdout/stderr ◄──────────────────┘
   → os.read(master_fd)
   → channel.write()
   ▼
SSH Client terminal
```

## Key Design Decisions

**One process per connection.** Each SSH session spawns a completely independent subprocess. This means there is no shared mutable state to protect — no locks, no cross-session coordination, no risk of one client's input corrupting another's app state. The tradeoff is higher resource usage at scale, but it makes the system simple to reason about and straightforward to test.

The alternative would be to run Textual app instances in-process and redirect their stdio/stdin to each SSH channel. That path requires either writing a custom Textual driver, patching Textual's internals to replace its I/O assumptions, or hooking into undocumented lifecycle APIs — all of which couple textish tightly to Textual's implementation details. The subprocess approach sidesteps this entirely: the app sees a real PTY and a real terminal, exactly as it would when run locally. This is also the approach taken by [textual-web](https://github.com/Textualize/textual-web), Textual's own remote-serving project, which gives some confidence that it is the right boundary to draw.

**PTY required.** Textual relies on a real terminal to render its layout — it queries terminal capabilities, emits ANSI escape sequences, and reads raw keystrokes. Without a PTY, the app would either crash or produce garbled output. Rejecting non-PTY connections early (with a clear error message) prevents silent failures and keeps the server's scope well-defined.

**FIFO input queue.** Client keystrokes are buffered in an `asyncio.Queue` and drained by a single consumer coroutine before being written to the PTY master. If multiple coroutines wrote concurrently, partial writes could interleave bytes — for example, two rapid keypresses could arrive at the subprocess in scrambled order. The single-consumer pattern eliminates this without needing locks.

**Graceful shutdown ordering.** `SessionManager` tracks every `AppSession.run()` task and cancels them all before the server exits. Without this, the event loop could stop while subprocesses were still running, leaving orphaned processes with no way to receive input or be cleaned up. Awaiting cancellation ensures every subprocess receives `SIGTERM` (and `SIGKILL` if it stalls) before the program exits.

**Auth abstraction.** The `auth` callback on `AppConfig` accepts any callable — sync or async — that takes a username and public key string and returns a bool. This keeps the core server logic decoupled from any particular key store or identity provider. `authorized_keys()` covers the common case out of the box, but callers can integrate LDAP, a database, or any async service without touching server internals.

## Technology Stack

| Layer         | Library                 | Version    |
| ------------- | ----------------------- | ---------- |
| SSH server    | asyncssh                | ≥2.22, <3 |
| TUI framework | textual                 | ≥0.58, <1 |
| Async runtime | asyncio (stdlib)        | —         |
| Language      | Python                  | ≥3.12     |
| Linting       | ruff                    | —         |
| Type checking | mypy (strict)           | —         |
| Testing       | pytest + pytest-asyncio | —         |

## Limitations

- **Unix only** — PTY management uses POSIX APIs (`pty`, `termios`, `fcntl`). Windows is not supported.
- **No reconnection** — disconnecting a client terminates the subprocess.
- **No session sharing** — clients cannot share a running app instance.
