# Architecture

textish serves independent Textual app instances over SSH from one Python process. Every interactive SSH session gets its own app object and `SSHDriver`; imported modules and the asyncio event loop are shared.

## Directory structure

```
textish/
├── src/textish/
│   ├── __init__.py           # Public API, server startup, auth and host keys
│   ├── config.py             # Validated AppConfig
│   ├── cli.py                # CLI entry point
│   ├── metrics.py            # Per-worker counters, latency samples and reporter
│   ├── server.py             # AsyncSSH sessions, limits and lifecycle
│   └── inprocess/
│       ├── __init__.py
│       ├── driver.py         # SSHDriver: terminal bytes ↔ Textual events
│       └── session.py        # One app instance's lifecycle
├── tests/
│   ├── unit/
│   └── integration/          # Real AsyncSSH client/server tests
└── examples/
```

## Components

### Public API

`serve(app, ...)` accepts an `App` subclass, any zero-argument factory, or a
`"module:attr"` reference. Direct factories do not need to be importable because
multi-worker mode forks before starting server event loops. `serve_async()`
supports both `app_factory` and `app_ref` for embedding one worker in an existing
event loop.

Server startup generates a stable Ed25519 host key with mode `0600` when needed. It defaults to localhost and warns if an unauthenticated server binds to a non-loopback address. Optional uvloop and coloured logging support are extras.

### `SessionManager`

The manager owns process-wide admission and lifecycle controls:

- SSH transport reservations enforce `max_ssh_connections` before sessions are
  opened, and a separate counter bounds concurrent authentication.
- Session reservations enforce `max_connections` across SSH channels, including
  multiple channels opened through one TCP connection.
- A configurable startup gate prevents a connection burst from continuously
  scheduling expensive Textual startup paths ahead of existing sessions.
- A separate pending-startup bound caps the total of apps currently starting
  and apps waiting for a startup slot. Both reservations are released when an
  app reaches Textual's ready state or its task ends.

It also owns the worker's runtime metrics, observes every app task, logs
failures, and cancels all remaining tasks during server shutdown.

### Runtime metrics

`ServerMetrics` keeps event-loop-local counters and bounded latency samples, so
the hot path needs no locks and memory usage does not grow with uptime. The
session manager supplies point-in-time admission and lifecycle counts. SSH
sessions and drivers record input/output bytes, disconnect causes, startup
latency, and the delay from client input to the first following render.

When configured, one reporter task per worker measures event-loop scheduling
lag and periodically emits a JSON snapshot or invokes the configured sync/async
callback. Metrics publishing is deliberately dependency-free; integrations can
translate the snapshot into Prometheus, OpenTelemetry, or another backend.

### `TextishSSHServerSession`

One instance represents one SSH shell channel. It:

- requires a PTY and records terminal dimensions;
- creates one `InProcessAppSession` after the shell starts;
- forwards input and resize callbacks synchronously in event-loop order;
- resets the optional idle timeout on client activity;
- limits AsyncSSH's channel output buffer to 256 KiB and disconnects a slow reader if the high-water callback fires; and
- clamps initial and updated terminal dimensions to configured bounds;
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

- `write()` encodes terminal output, writes directly to one AsyncSSH channel,
  and reports successfully submitted byte counts;
- `feed()` incrementally decodes one client's bytes and passes parsed events to Textual;
- `resize()` posts a Textual resize message; and
- application-mode lifecycle methods manage alternate-screen, mouse, cursor, and bracketed-paste escape sequences.

There are no per-session threads, subprocesses, polling loops, or serialization queues.
The binding uses one shared driver class rather than creating a new Python type
for every connection, and the compact pre-start input buffer grows only when
bytes arrive.

## Data flow

```
TCP connection  [reserve SSH transport slot]
  → authentication  [reserve authentication slot]
  → SSH session_requested()  [reserve global session slot]
  → PTY + shell requested
  → admit pending startup
  → create app instance and run task  [concurrent startup gate]

client bytes → data_received → SSHDriver.feed → this App instance
client resize → terminal_size_changed → SSHDriver.resize → this App instance
App render → SSHDriver.write → AsyncSSH channel → client

session/driver events → ServerMetrics → periodic JSON log or callback

EOF/disconnect → cancel app task → Textual driver cleanup → close channel
```

## Scaling model

Sharing imported modules keeps the baseline per-session overhead low, but widget
trees, render caches, terminal size, and application state can change memory use
substantially.

The trade-off is cooperative scheduling. All apps share one event loop and GIL:

- async I/O scales well when handlers yield promptly;
- blocking I/O should use async libraries, Textual workers, or `asyncio.to_thread()`;
- CPU-heavy work should use a process pool or external service; and
- mutable module globals must not be used as unkeyed per-user state.

A Python exception in one app task is contained and closes that session, but a process-level failure affects everyone.

For workloads beyond one event loop's latency or memory budget, `serve()` can
fork multiple workers. Every worker binds the same listener with `SO_REUSEPORT`,
owns an independent `SessionManager`, event loop, GIL, and heap, and presents the
same host key. The parent process watches for unexpected worker exits and sends
workers an interrupt for graceful app cleanup before terminating stragglers.

All admission settings are per worker. Cross-worker mutable state must live in
an external store, and capacity beyond one machine should be distributed with
an L4 load balancer. `serve_async()` stays single-worker because its event loop
is owned by the embedding application.

The load harness follows the same production path: real SSH handshakes and PTY
channels against one or more worker processes. It can import a deployment app,
hold mixed active/idle populations, churn sessions, send hostile resize values,
run a soak scenario, enforce latency budgets, and write JSON results. A compact
multi-worker scenario runs in the normal pytest suite as a regression guard.

## Compression

AsyncSSH's default algorithm list already offers delayed `zlib@openssh.com`; clients can negotiate it with `ssh -C`. Compression is not forced because it trades bandwidth for CPU time and per-connection compressor state. Evaluate it against the deployment's actual terminal output and network conditions.

## Security responsibilities

textish is responsible for SSH authentication hooks, private host-key handling, transport limits, idle cleanup, and slow-client backpressure. Served applications are responsible for domain authorization and validating user-controlled values. Application code itself must be trusted because all sessions share the process.

## Supported platform

- Python 3.12+
- Linux and macOS
- AsyncSSH ≥2.24, <3
- Textual ≥8.2, <9
