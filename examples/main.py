"""Minimal example: import your Textual app and serve it over SSH.

Run it (from the repo root or from this directory)::

    python examples/main.py
    # then, from another terminal:
    ssh -p 2222 localhost

``serve`` generates a host key on first run and manages everything else. The app
is imported from ``examples/app.py``; it must live in an importable module (not
inline here), because each connection re-imports it in its own subinterpreter.
Connect with a real terminal (a PTY is required).
"""

from app import WordleApp  # examples/app.py

from textish import serve

if __name__ == "__main__":
    serve(WordleApp, host="127.0.0.1", port=2222)
