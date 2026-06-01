"""Runtime console/file logging helpers."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "log"


class TeeStream:
    """Write text to multiple streams."""

    def __init__(self, *streams: TextIO) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()

    def isatty(self) -> bool:
        return any(getattr(stream, "isatty", lambda: False)() for stream in self.streams)


def setup_runtime_log(log_dir: Path = LOG_DIR) -> Path:
    """Mirror stdout/stderr into a minute-stamped runtime log file."""

    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%m_%d_%Y-%H_%M")
    log_file = log_dir / f"SpaceAvoider_log_{timestamp}.log"
    file_stream = log_file.open("w", buffering=1, encoding="utf-8")

    sys.stdout = TeeStream(sys.__stdout__, file_stream)  # type: ignore[assignment]
    sys.stderr = TeeStream(sys.__stderr__, file_stream)  # type: ignore[assignment]
    print(f"[logging] writing runtime log to {log_file}", flush=True)
    return log_file
