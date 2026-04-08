import sys
from textish import serve

python = sys.executable
serve(f"{python} examples/app.py", port=2222)
