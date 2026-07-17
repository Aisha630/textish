# Architecture

textish serves independent Textual app instances over SSH from one Python process. Every interactive SSH session gets its own app object and `SSHDriver`; imported modules and the asyncio event loop are shared.

## Directory structure

```
textish/
├── src/textish/
│   ├── __init__.py           # Public API, server startup, auth and host keys
│   ├── config.py             # Validated AppConfig
│   ├── cli.py                # CLI entry point
│   ├── server.py             # AsyncSSH sessions, limits and lifecycle
│   └── inprocess/
│       ├── __init__.py
│       ├── driver.py         # SSHDriver: terminal bytes ↔ Textual events
│       └── session.py        # One app instance's lifecycle
├── tests/
│   ├── unit/
│   └── integration/          # Real AsyncSSH client/server tests
├── examples/
└── benchmarks/
    ├── bench_app.py
    ├── bench_shared.py       # Synthetic in-process session benchmark
    └── bench_ssh.py          # Real encrypted SSH connection benchmark
```

## Components

### Public API

`serve(app, ...)` accepts an `App` subclass, any zero-argument factory, or a `"module:attr"` reference. Direct factories do not need to be importable because they stay in the server interpreter. `serve_async(AppConfig(...))` supports both `app_factory` and `app_ref` for embedding in an existing event loop.

Server startup generates a stable Ed25519 host key with mode `0600` when needed. It defaults to localhost and warns if an unauthenticated server binds to a non-loopback address. Optional uvloop and coloured logging support are extras.

### `SessionManager`

The manager owns two process-wide controls:

- A session reservation count enforces `max_connections` across SSH channels, including multiple channels opened through one TCP connection.
- A four-slot startup gate prevents a connection burst from continuously scheduling hundreds of expensive Textual startup paths ahead of existing sessions. A slot is released when an app reaches Textual's ready state.

It also observes every app task, logs failures, and cancels all remaining tasks during server shutdown.

### `TextishSSHServerSession`

One instance represents one SSH shell channel. It:

- requires a PTY and records terminal dimensions;
- creates one `InProcessAppSession` after the shell starts;
- forwards input and resize callbacks synchronously in event-loop order;
- resets the optional idle timeout on client activity;
- limits AsyncSSH's channel output buffer to 256 KiB and disconnects a slow reader if the high-water callback fires; and
- cancels only its own app task on EOF or disconnect.

### `InProcessAppSession`

The session calls the configured factory to create a fresh `App`, binds its
driver, and awaits `app.run_async()`. Input arriving during startup is bounded
to 64 KiB and drained after the driver enters application mode. Terminal resizes
received during startup are applied when the driver becomes ready. Exceptions
are logged and reported briefly to the SSH client.

Textual normally redirects process-global `sys.stdout` and `sys.stderr` for the lifetime of an app. That is unsafe when app lifetimes overlap, so server mode disables those global swaps. `print()` output remains attached to the server process; applications should use logging for diagnostics.

### `SSHDriver`

`SSHDriver` replaces Textual's terminal driver assumptions:

- `write()` encodes terminal output and writes directly to one AsyncSSH channel;
- `feed()` incrementally decodes one client's bytes and passes parsed events to Textual;
- `resize()` posts a Textual resize message; and
- application-mode lifecycle methods manage alternate-screen, mouse, cursor, and bracketed-paste escape sequences.

There are no per-session threads, subprocesses, polling loops, or serialization queues.
The binding uses one shared driver class rather than creating a new Python type
for every connection, and the compact pre-start input buffer grows only when
bytes arrive.

## Data flow

```
TCP connection
  → SSH session_requested()  [reserve global session slot]
  → PTY + shell requested
  → create app instance and run task  [startup gate]

client bytes → data_received → SSHDriver.feed → this App instance
client resize → terminal_size_changed → SSHDriver.resize → this App instance
App render → SSHDriver.write → AsyncSSH channel → client

EOF/disconnect → cancel app task → Textual driver cleanup → close channel
```

## Scaling model

Sharing imported modules keeps the baseline per-session overhead low. The
included synthetic benchmark measured roughly 430–440 KB per small app session on
one macOS/Python 3.14 machine, but widget trees, render caches, terminal size,
and application state can change that substantially.

The real-SSH benchmark measured about 464 KB of server memory per connection at
1,000 concurrent encrypted localhost connections on the same machine. This
includes AsyncSSH connection and channel state but not the separate client load
generator.

The trade-off is cooperative scheduling. All apps share one event loop and GIL:

- async I/O scales well when handlers yield promptly;
- blocking I/O should use async libraries, Textual workers, or `asyncio.to_thread()`;
- CPU-heavy work should use a process pool or external service; and
- mutable module globals must not be used as unkeyed per-user state.

A Python exception in one app task is contained and closes that session, but a process-level failure affects everyone.

## Compression

AsyncSSH's default algorithm list already offers delayed `zlib@openssh.com`; clients can negotiate it with `ssh -C`. Compression is not forced because it trades bandwidth for CPU time and per-connection compressor state. This should be benchmarked against the deployment's actual terminal output and network conditions.

## Security responsibilities

textish is responsible for SSH authentication hooks, private host-key handling, transport limits, idle cleanup, and slow-client backpressure. Served applications are responsible for domain authorization and validating user-controlled values. Application code itself must be trusted because all sessions share the process.

## Supported platform

- Python 3.12+
- Linux and macOS
- AsyncSSH ≥2.24, <3
- Textual ≥8.2, <9
