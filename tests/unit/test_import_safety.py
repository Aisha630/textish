"""Keep the top-level API lightweight."""

import subprocess
import sys


def test_importing_textish_does_not_import_asyncssh():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import textish; print('asyncssh' in sys.modules)",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.stdout.strip() == "False"
