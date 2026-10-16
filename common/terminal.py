"""UTF-8 terminal output for the Windows lab environment."""

import os
import sys

from rich.console import Console


def create_console() -> Console:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    return Console()
