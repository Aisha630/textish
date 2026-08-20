# textish

[![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Built with asyncssh](<https://img.shields.io/badge/built%20with-asyncssh-4a90d9>)](https://asyncssh.readthedocs.io/)
[![Powered by Textual](<https://img.shields.io/badge/powered%20by-Textual-41337a>)](https://github.com/Textualize/textual)

![textish demo](demo.gif)

Serve [Textual](https://github.com/Textualize/textual) TUI apps over SSH. Import your Textual app, hand it to `serve`, and anyone with an SSH client can connect and use the app in their terminal — no installation required on their end.

Each SSH session gets a fresh app instance and driver on one shared asyncio event loop. That keeps per-user input and screen state separate without re-importing Textual or starting a thread/process for every connection.

```python
# run.py
from textish import serve
from myapp import MyApp   # your Textual App, in an importable module

serve(MyApp, port=2222)
```

```
python run.py
# then, from another terminal:
ssh localhost -p 2222
```

---

## How it works

Textual talks to the outside world through a `Driver`. The stock drivers assume a real terminal: they read `sys.stdin`, write `sys.stdout`, set `termios`, and install signal handlers. textish replaces that with an `SSHDriver` whose terminal is one AsyncSSH channel.

For each interactive SSH session:

- A fresh app instance is constructed from the class, factory, or import reference.
- A session-specific `SSHDriver` writes rendered bytes directly to its SSH channel and parses only that client's input.
- All sessions share the server's modules and asyncio loop. There are no per-session threads, subprocesses, polling loops, or cross-interpreter queues.
- SSH transports, authentication, not-yet-ready apps, and concurrent app
  startups have separate admission limits. Slow clients are disconnected when
  their SSH output buffer reaches the safety limit.
- Client-controlled terminal dimensions and SSH channel receive windows are
  bounded so one session cannot request an arbitrarily expensive screen or
  input buffer.

This is the same idea as [wish](https://github.com/charmbracelet/wish) (Charmbracelet's SSH app framework for Go): the app is imported and run in-process rather than launched as a subprocess.

### Why a shared interpreter?

textish is designed for trusted Textual applications, such as dashboards,
browsers, administration tools, and controlled interactive apps—not for hosting
arbitrary user-supplied Python code. Earlier subinterpreter experiments consumed
substantially more memory per session, added queues and lifecycle complexity,
and still did not provide a security sandbox.

The shared model keeps the useful isolation: every SSH session receives a fresh
app object, driver, input stream, terminal size, and screen state. It deliberately
shares imported modules, process globals, the GIL, and the asyncio event loop.
This makes thousands of connections much lighter, with two requirements for the
served app:

- Keep per-user mutable state on the app instance or in a store keyed by user.
- Keep handlers non-blocking and move CPU-heavy work to another process or
  service.

For the component design and data flow, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Installation

Requires Python 3.12 or later.

```
pip install textish
```

Optional coloured logs and uvloop support are available as extras:

```
pip install "textish[color,performance]"
```

---

## Usage

Pass `serve` your `App` subclass, any zero-argument factory, or a `"module:attr"` string. Classes and factories may be defined in the script itself. A fresh app instance is created for every session. `serve` blocks until interrupted and generates a host key on first run.

### Command line

```
textish my_package.my_module:MyApp
textish my_package.my_module:MyApp --port 3000
textish my_package.my_module:MyApp --host 127.0.0.1 --port 3000 --max-connections 10
textish my_package.my_module:MyApp --workers 4 --max-ssh-connections 500 --max-startups 8
```

Run `textish --help` for all options. The main controls are `--host`, `--port`,
`--host-key`, `--workers`, `--max-connections`, `--max-ssh-connections`,
`--max-authenticating`, `--max-startups`, `--max-pending-startups`,
`--idle-timeout`, `--metrics-interval`, and `--authorized-keys`. Use
`--log-level DEBUG` (or `-v`) for detailed server logs.

### Python API

```python
from textish import serve
from myapp import MyApp

serve(MyApp, port=2222, max_connections=10)
```

For a public production listener, set finite transport and session limits. The
startup controls protect responsiveness during bursts:

```python
serve(
    MyApp,
    host="0.0.0.0",
    workers=4,
    max_ssh_connections=500,
    max_connections=500,
    max_authenticating=32,
    max_startups=8,
    max_pending_startups=64,
    idle_timeout=1800,
    metrics_interval=10,
    auth=my_auth,
)
```

`max_connections` counts SSH session channels; `max_ssh_connections` counts
SSH transports. One transport may open multiple channels. Pending startups
include apps currently starting and apps waiting for a startup slot. When the
pending limit is full, new app sessions receive a short busy message and close.
All resource limits are per worker, so the example permits approximately 2,000
SSH transports across four workers. Workers share the listening port through
the operating system and each owns an independent event loop and Python heap.
Call multi-worker `serve()` from the main process before starting application
threads.

`serve` blocks and runs its own event loop. If you are embedding textish in a program that already has a running loop, build an `AppConfig` and use the async entry point instead:

```python
from textish import AppConfig, serve_async

await serve_async(AppConfig(app_ref="myapp:MyApp", port=2222))
```

The async entry point generates the host key when needed, just like `serve`.
`serve_async()` requires `workers=1`; programs embedding an existing event loop
should run multiple application processes with their process supervisor instead.

### Runtime metrics

Set `metrics_interval` to emit one compact JSON snapshot per worker at the
requested interval. The process ID in each snapshot identifies its worker:

```python
serve(MyApp, workers=4, metrics_interval=10)
```

Snapshots include current SSH, authentication, session, startup, and app-task
counts; admission rejections; idle and slow-reader disconnects; app failures;
input/output byte totals; startup and input-to-render latency; and event-loop
lag. To send these values to an existing telemetry system, supply a synchronous
or asynchronous callback instead of using the default JSON logger:

```python
from textish import MetricsSnapshot, serve

async def publish_metrics(snapshot: MetricsSnapshot) -> None:
    await telemetry.write(snapshot)

serve(
    MyApp,
    metrics_interval=10,
    metrics_callback=publish_metrics,
)
```

Callbacks run on the worker event loop, so they should return promptly and use
non-blocking I/O. Counters and latency samples are per worker.

### Host keys

`serve` generates `~/.ssh/textish_host_key` with private permissions on first run. To use a specific key, pass a path; it is generated there if missing:

```python
serve(MyApp, port=2222, host_key_path="/etc/textish/ssh_host_key")
```

Or generate one yourself:

```
ssh-keygen -t ed25519 -f ~/.ssh/textish_host_key -N ""
```

### Public-key authentication

By default, textish listens only on `127.0.0.1` and allows connections without authentication. If you bind to a non-loopback interface, configure authentication; textish logs a warning when a public bind has no authentication callback.

```python
ALLOWED_KEYS = {"ssh-ed25519 AAAAC3Nza..."}

def auth(username: str, public_key: str) -> bool:
    return public_key in ALLOWED_KEYS

serve(MyApp, port=2222, auth=auth)
```

The function receives the username and the client's public key in OpenSSH format. It may also be `async`. See also `authorized_keys()` for reading an OpenSSH `authorized_keys` file.

---

## Examples

The `examples/` directory has two runnable apps:

- `examples/main.py` — a small Wordle game (`python examples/main.py`).
- `examples/serve_docbrowser.py` — a document browser with a clickable sidebar and a long, scrollable Markdown pane, showing text rendering, scrolling, and mouse interaction (`python examples/serve_docbrowser.py`). Use a large terminal window.

Both start a server on `127.0.0.1:2222`; connect with `ssh -p 2222 localhost`.

---

## Performance

Sessions share imported code but keep independent app and widget state. Memory
use therefore depends mainly on the app tree, terminal size, and data retained
for each user.

All apps share one event loop and GIL. Keep event handlers short and non-blocking,
use async I/O, and move blocking work to a Textual worker or
`asyncio.to_thread()`. CPU-heavy work should use a process pool or external
service. A blocking handler can delay every connected user.

### Capacity testing

The repository includes a self-contained load harness which runs the server in
an isolated process. It performs real SSH handshakes, opens PTYs, waits for a
Textual app to render, holds every session open, and measures a simultaneous
input-to-render burst:

```
uv run --frozen python benchmarks/session_load.py
uv run --frozen python benchmarks/session_load.py --workers 4 --sessions 100 500 1000
uv run --frozen python benchmarks/session_load.py \
  --workers 2 --sessions 100 --max-input-p95-ms 250
uv run --frozen python benchmarks/session_load.py \
  --app-ref myapp:MyApp --ready-text "READY" \
  --input-text x --response-text "UPDATED" --active-ratio 0.2 \
  --churn-ratio 0.05 --resize 1000x1000 --soak-seconds 300 \
  --json-output benchmark.json
```

It reports server RSS, startup p50/p95, interaction p50/p95, and replacement
startup p95 at each target. `--active-ratio` models a mix of active and idle
sessions, `--churn-ratio` replaces a portion of the population each round,
`--resize` exercises terminal-size clamping, and `--soak-seconds` repeats the
interaction scenario. `--json-output` writes a machine-readable regression
artifact. A real app scenario may omit `--input-text` and `--response-text` to
measure startup and holding cost only.

Use the results to set per-worker limits with memory and latency headroom. The
default benchmark app is intentionally tiny, so applications with larger widget
trees should be profiled under their expected data volume and terminal sizes.

A small two-worker benchmark is also part of the normal pytest suite, so every
local and CI test run covers real handshakes, PTYs, app import by reference,
startup, mixed active/idle input, rendering, churn, an oversized resize, JSON
reporting, and worker shutdown. Its latency budgets are intentionally loose to
catch hangs and major regressions without making shared CI hardware flaky; use
the full harness with deployment-specific budgets for capacity work.

### Admission controls

Defaults allow unlimited live transports and sessions for local development,
but bound the expensive transition into a ready app:

- 64 transports authenticating concurrently;
- 4 apps starting concurrently and 64 not-yet-ready apps;
- a 64 KiB SSH receive window and 256 KiB slow-reader output limit; and
- terminal dimensions clamped to 240 columns by 80 rows.

These values are configurable through `serve()`, `AppConfig`, and the CLI.
Production deployments should set finite `max_ssh_connections` and
`max_connections`, then increase `workers` when one event loop no longer meets
the interaction-latency target. Multiple hosts can sit behind an L4 load
balancer for capacity beyond one machine.

### SSH compression

AsyncSSH already advertises delayed `zlib@openssh.com` compression. Clients which benefit from compression can request it with `ssh -C`. textish does not force compression because ANSI screen updates are often small or repetitive already, while compression adds CPU and per-connection state. Test it with your own traffic before enabling it broadly.

---

## Security

textish owns transport security and resource safety: SSH host keys, authentication hooks, session limits, idle timeouts, and bounded output buffering. Your app owns domain authorization and safe handling of user input—for example, deciding which records a username may view and validating values before using them in database queries or commands.

The shared interpreter is not a security boundary. Serve only trusted application
code and do not use textish to execute arbitrary commands or untrusted Python.
Even view-only applications must enforce which records each authenticated user
may access. textish secures and limits the SSH transport; it cannot infer an
application's domain permissions.

---

## Limitations

**PTY required.** textish only supports interactive shell sessions with a pseudo-terminal. Clients that connect without a PTY (for example, `ssh host -p 2222 some-command`) are rejected with an error message.

**Shared event loop.** Blocking or CPU-heavy app code affects all sessions.

**Shared process.** All sessions share one OS process. A whole-process fault (a C-extension crash, out-of-memory) affects every session, unlike a one-process-per-connection design.

**Shared module state.** App instances are separate, but imported modules, class variables, caches, and other process globals are shared.

**Server-side standard streams.** Textual's process-wide stdout/stderr capture
is disabled because concurrent apps cannot safely replace shared streams.
`print()` writes to the server terminal, not to an SSH client; use logging for
diagnostics and widgets for client-visible output.

---

## Development

Install with dev dependencies:

```
poetry install --with dev
```

Run the tests:

```
poetry run pytest
```

Lint and type-check:

```
poetry run ruff check .
poetry run mypy
```
