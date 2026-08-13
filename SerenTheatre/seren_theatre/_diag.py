"""Startup diagnostics that survive a nulled stdout.

Parity with SerenMargin/_diag.py. The reason this exists at all is worth
repeating where someone will read it: a service that writes to a stream nobody
is reading is fine right up until the stream is CLOSED, at which point the
write raises and takes the process with it. `pythonw` on Windows nulls
sys.stdout and sys.stderr, and the first library that logs a line kills the
server. Hide the window, never the stream.

So: print, but never let printing be the thing that ends the run.
"""
from __future__ import annotations

import sys


def diag(message: str) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None:
                continue
            stream.write(message + "\n")
            stream.flush()
            return
        except Exception:  # noqa: BLE001 - a broken stream must not be fatal
            continue
