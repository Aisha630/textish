import sys

from textish import serve

# sys.executable ensures the subprocess uses the same venv Python as the server
serve(f"{sys.executable} examples/app.py", port=2222)
