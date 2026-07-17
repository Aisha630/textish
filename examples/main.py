"""Minimal example: import your Textual app and serve it over SSH.

Run it (from the repo root or from this directory)::

    python examples/main.py
    # then, from another terminal:
    ssh -p 2222 localhost

``serve`` generates a host key on first run and manages everything else. Each
SSH session receives a fresh ``WordleApp`` instance in the shared interpreter.
Connect with a real terminal (a PTY is required).
"""

from app import WordleApp  # examples/app.py

from textish import serve

if __name__ == "__main__":
    serve(WordleApp, host="127.0.0.1", port=2222)
