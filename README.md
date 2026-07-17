# textish

[![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Built with asyncssh](https://img.shields.io/badge/built%20with-asyncssh-4a90d9)](https://asyncssh.readthedocs.io/)
[![Powered by Textual](https://img.shields.io/badge/powered%20by-Textual-41337a)](https://github.com/Textualize/textual)

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
- Startup concurrency is bounded so a burst of new clients does not monopolize the event loop. Slow clients are disconnected when their SSH output buffer reaches the safety limit.

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
```

Run `textish --help` for all options. The main controls are `--host`, `--port`,
`--host-key`, `--max-connections`, `--idle-timeout`, and `--authorized-keys`.
Use `--log-level DEBUG` (or `-v`) for detailed server logs.

### Python API

```python
from textish import serve
from myapp import MyApp

serve(MyApp, port=2222, max_connections=10)
```

`serve` blocks and runs its own event loop. If you are embedding textish in a program that already has a running loop, build an `AppConfig` and use the async entry point instead:

```python
from textish import AppConfig, serve_async

await serve_async(AppConfig(app_ref="myapp:MyApp", port=2222))
```

The async entry point generates the host key when needed, just like `serve`.

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

Sessions share imported code but keep independent app/widget state. Memory therefore depends mainly on the app tree and terminal size. Measure your app with [`benchmarks/bench_shared.py`](benchmarks/bench_shared.py):

```
python benchmarks/bench_shared.py --sessions 100
python benchmarks/bench_shared.py --sessions 1000
python benchmarks/bench_shared.py --sessions 100 --work 200000
```

On one macOS/Python 3.14 run, the small benchmark app used about 430–440 KB per
live session: roughly 43 MB for 100 and 420 MB for 1,000. Those are not
guarantees; real apps and encrypted SSH connections may use much more.

For an end-to-end measurement with real TCP sockets, SSH handshakes,
encryption, channels, and Textual apps, run:

```
python benchmarks/bench_ssh.py --sessions 100
python benchmarks/bench_ssh.py --sessions 1000 --connect-concurrency 100 --hold 5
```

The SSH server runs in a child process, so the report separates server memory
from the client load generator. The benchmark raises its soft file-descriptor
limit when the operating system allows it. Start with 100 sessions before
running the 1,000-session case.

On one local macOS/Python 3.14 run, 1,000 uncompressed encrypted connections
rendered successfully in 7.4 seconds. The server used about 453 MB above its
baseline (464 KB per connection), while the separate client load generator used
about 37 MB. Localhost results do not model internet latency or bandwidth.

All apps share one event loop and GIL. Keep event handlers short and non-blocking,
use async I/O, and move blocking work to a Textual worker or
`asyncio.to_thread()`. CPU-heavy work should use a process pool or external
service. A blocking handler can delay every connected user.

### SSH compression

AsyncSSH already advertises delayed `zlib@openssh.com` compression. Clients which benefit from compression can request it with `ssh -C`. textish does not force compression because ANSI screen updates are often small or repetitive already, while compression adds CPU and per-connection state. Benchmark it with your traffic before requiring it.

Compare uncompressed and compressed SSH sessions locally with:

```
python benchmarks/bench_ssh.py --sessions 100 --compression none
python benchmarks/bench_ssh.py --sessions 100 --compression zlib
```

In the same local 100-connection test, zlib increased server memory from about
47 MB to 63 MB without improving startup time. Remote low-bandwidth links may
still benefit, which is why the benchmark exposes both modes.

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
