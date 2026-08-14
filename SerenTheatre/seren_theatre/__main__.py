"""Entry point for `python -m seren_theatre` or the `seren-theatre` script.

Accepts --config / -c to match the SerenMemory convention (Memory leads, the
rest follow), so the installer can pass the config path explicitly and a buddy
who learned one service knows this one.

--describe exists here as well as in the installer because Starwright runs it
on `seren-*-setup.sh`, and having the SERVICE able to answer the same question
means the two can be checked against each other. That is not belt-and-braces:
the `seren/port-map` fact records that Workbench's code said 7425 while its
installer said 7444, so an installed node answered where the docs did not. Two
sources that can disagree are only useful if something compares them.
"""
from __future__ import annotations

import argparse
import json
import sys


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 regardless of OS locale.

    Parity with SerenMargin/SerenLoci. On Windows the console defaults to a
    legacy codepage, so a smart quote in a config path can raise
    UnicodeEncodeError mid-work. PYTHONUTF8=1 in the service env is the primary
    fix; this is the backstop for the hand-run case.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="seren_theatre",
        description="SerenTheatre - the room where you watch the thing being made.")
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to seren-theatre.yaml (default: $SEREN_THEATRE_CONFIG, then "
             "~/seren-theatre/seren-theatre.yaml, falling back to built-in "
             "defaults).")
    args = parser.parse_args()

    # Everything heavy is imported LATE, below the --describe return above.
    import uvicorn

    from ._diag import diag
    from .app import create_app
    from .config import load_config

    cfg = load_config(args.config)
    app = create_app(cfg)

    diag(f"[seren-theatre] listening on {cfg.host}:{cfg.port}  -> "
         f"http://{cfg.host}:{cfg.port}/viewer")
    if not cfg.stages:
        diag("[seren-theatre] no stages configured - the room is empty. Set "
             "SEREN_THEATRE_STAGE=/path/to/lab or add a stages: block.")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
