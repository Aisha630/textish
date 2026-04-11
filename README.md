# textish

[![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Built with asyncssh](https://img.shields.io/badge/built%20with-asyncssh-4a90d9)](https://asyncssh.readthedocs.io/)
[![Powered by Textual](https://img.shields.io/badge/powered%20by-Textual-41337a)](https://github.com/Textualize/textual)

Serve [Textual](https://github.com/Textualize/textual) TUI apps over SSH. Point it at any command that runs a Textual app, give it a port, and anyone with an SSH client can connect and use the app in their terminal — no installation required on their end.

```python
from textish import serve

serve("python my_app.py", port=2222)
```

```
ssh localhost -p 2222
```

---

## How it works

Textual ships with a `WebDriver` that runs an app in a subprocess and communicates over pipes using a simple length-prefixed packet protocol. textish reuses that mechanism entirely. Each SSH connection spawns the Textual app as a fresh subprocess and bridges the SSH channel to the subprocess's stdin/stdout. Nothing inside Textual is patched or monkeypatched — the app has no idea it's being served over SSH.

This is the same basic idea as [wish](https://github.com/charmbracelet/wish) (Charmbracelet's SSH app framework for Go) and [inkish](https://github.com/Textualize/inkish), adapted to work with Textual's existing driver infrastructure.

---

## Installation

Requires Python 3.12 or later.

```
pip install textish
```

---

## Usage

### Basic

```python
from textish import serve

serve("python my_app.py", port=2222)
```

### Async

If you are already inside a running event loop (for example, embedding textish inside a larger async application):

```python
from textish import serve_async

await serve_async("python my_app.py", port=2222)
```

### Host keys

By default, textish looks for a host key at `~/.ssh/ssh_host_key`. You can generate one with:

```
ssh-keygen -t ed25519 -f ssh_host_key -N ""
```

Or pass explicit paths:

```python
serve("python my_app.py", port=2222, host_keys=["./ssh_host_key"])
```

---

## Limitations

A few things worth knowing before you deploy this anywhere serious.

**One process per connection.** Every SSH connection spawns a completely independent subprocess running your app. There is no shared state between clients, and no concept of a persistent session. If a client disconnects and reconnects, they get a brand new app instance from scratch.

**No reconnection support.** Related to the above — if a client's connection drops mid-session, there is nothing to reconnect to. The subprocess is terminated and any in-progress state is gone.

**PTY required.** textish only supports interactive shell sessions with a pseudo-terminal. Clients that connect without a PTY (for example, `ssh host -p 2222 some-command`) will be rejected with an error message. This is a deliberate constraint, not something that is straightforward to lift.

**The app must use Textual's WebDriver.** textish sets `TEXTUAL_DRIVER=textual.drivers.web_driver:WebDriver` in the subprocess environment. If your app overrides the driver or uses something incompatible, the handshake will fail and the connection will be dropped.

**Single-platform.** textish has only been tested on Linux and macOS. 

---

## Development

Install with the dev dependencies:

```
pip install -e ".[dev]"
```

Run the tests:

```
pytest
```

Lint:

```
ruff check src
```
