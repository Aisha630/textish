"""Fast load-harness smoke coverage included in every normal test run."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_multiworker_session_benchmark_smoke(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[2]
    report_path = tmp_path / "benchmark.json"
    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/session_load.py",
            "--sessions",
            "4",
            "8",
            "--workers",
            "2",
            "--app-ref",
            "benchmarks.session_load:BenchmarkApp",
            "--ready-text",
            "TEXTISH_READY",
            "--input-text",
            "z",
            "--response-text",
            "TEXTISH_ECHO:z",
            "--active-ratio",
            "0.5",
            "--churn-ratio",
            "0.25",
            "--resize",
            "1000x1000",
            "--connect-concurrency",
            "4",
            "--max-pending-startups",
            "8",
            "--max-startup-p95-ms",
            "5000",
            "--max-input-p95-ms",
            "5000",
            "--json-output",
            str(report_path),
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "workers=2" in result.stdout
    assert "startup_p95_ms" in result.stdout
    assert "input_p95_ms" in result.stdout
    report = json.loads(report_path.read_text())
    assert report["app_ref"] == "benchmarks.session_load:BenchmarkApp"
    assert report["active_ratio"] == 0.5
    assert report["churn_ratio"] == 0.25
    assert report["server_session_capacity"] == 10
    assert len(report["rounds"]) == 2
    assert report["failures"] == []
