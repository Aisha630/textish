"""Serve the document browser example over SSH.

Run it (from the repo root or this directory)::

    python examples/serve_docbrowser.py
    # then, from another terminal (use a large window):
    ssh -p 2222 localhost
"""

from docbrowser import DocBrowser  # examples/docbrowser.py

from textish import serve

if __name__ == "__main__":
    serve(DocBrowser, host="127.0.0.1", port=2222)
