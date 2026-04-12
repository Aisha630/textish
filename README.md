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

### Command line

```
textish "python my_app.py"
textish "python my_app.py" --port 3000
textish "python my_app.py" --host 127.0.0.1 --port 3000 --max-connections 10
```

```
$ textish --help
usage: textish [-h] [--host HOST] [--port PORT] [--host-key PATH]
               [--max-connections N]
               app_command

Serve a Textual app over SSH.

positional arguments:
  app_command           Shell command that launches your Textual app,
                        e.g. "python my_app.py".

options:
  --host HOST           Address to listen on. (default: 0.0.0.0)
  --port PORT           TCP port to listen on. (default: 2222)
  --host-key PATH       Path to the SSH host key file.
                        Defaults to ~/.ssh/ssh_host_key.
  --max-connections N   Maximum simultaneous SSH sessions.
                        0 means unlimited. (default: 0)
```

### Python API

```python
from textish import serve

# Note: requires a host key at ~/.ssh/ssh_host_key by default
serve("python my_app.py", port=2222)
```

#### Async

If you are already inside a running event loop (for example, embedding textish inside a larger async application):

```python
from textish import serve_async

await serve_async("python my_app.py", port=2222)
```

#### Configuration object

```python
from textish import serve_config, AppConfig

config = AppConfig(
    app_command="python my_app.py",
    port=2222,
    max_connections=10,
)
serve_config(config)
```

### Host keys

By default, textish looks for a host key at `~/.ssh/ssh_host_key`. You can generate one with:

```
ssh-keygen -t ed25519 -f ssh_host_key -N ""
```

Or pass an explicit path:

```python
serve("python my_app.py", port=2222, host_keys=["./ssh_host_key"])
```

### Public-key authentication

By default, textish allows all connections without authentication — suitable for private networks. To restrict access, pass an `auth_function`:

```python
ALLOWED_KEYS = {
    "ssh-ed25519 AAAAC3Nza..."}

def auth(username: str, public_key: str) -> bool:
    return public_key in ALLOWED_KEYS

serve("python my_app.py", port=2222, auth_function=auth)
```

The function receives the username and the client's public key in OpenSSH format. It may also be `async`.

---

## Limitations

A few things worth knowing before you deploy this anywhere serious.

**One process per connection.** Every SSH connection spawns a completely independent subprocess running your app. There is no shared state between clients, and no concept of a persistent session. If a client disconnects and reconnects, they get a brand new app instance from scratch.

**No reconnection support.** Related to the above — if a client's connection drops mid-session, there is nothing to reconnect to. The subprocess is terminated and any in-progress state is gone.

**PTY required.** textish only supports interactive shell sessions with a pseudo-terminal. Clients that connect without a PTY (for example, `ssh host -p 2222 some-command`) will be rejected with an error message. This is a deliberate constraint, not something that is straightforward to lift.

**The app must use Textual's WebDriver.** textish sets `TEXTUAL_DRIVER=textual.drivers.web_driver:WebDriver` in the subprocess environment. If your app overrides the driver or uses something incompatible, the handshake will fail and the connection will be dropped.

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

Lint:

```
poetry run ruff check .
```

Type check:

```
poetry run mypy
```
