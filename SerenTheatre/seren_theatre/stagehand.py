"""Stagehand - the one that does the work.

    "Stagehand does the work, cause they do fuckin everything, and the theatre
     shows the data."

THE CONFLICT THIS FILE RESOLVES, up front, because it decided the whole design.

Starting a build is a WRITE. Theatre has a structural test - test_no_route_can_write
in tests/test_app.py - asserting the service exposes no POST, PUT, PATCH or
DELETE anywhere. That test is not decoration; it is what makes it safe to point
Theatre at a live 14B run that has been going nine hours.

So the obvious shape - POST /build on the viewer - is forbidden, and it is
forbidden by the ethos before it is forbidden by the test. If the theatre could
start the build, the theatre would be doing the work. A stagehand is not on
stage.

Therefore stagehand is a SIBLING COMMAND, not a route:

    seren-theatre-stagehand recipe.yaml

It ships in the same wheel, behind the [stagehand] extra, and the service never
imports it. The room stays a room. The person with the terminal starts the run;
the room shows it. Those are different jobs done by different things, which is
the entire point of the name.

FORK, NEVER IMPORT - twice over.

Stagehand runs the literal string from ms-moe's README:

    ms-moe build recipe.yaml

Not an import of ms_moe.runner, not a Python API with its own defaults. The
same command a person types. If the automated path and the hand-run path ever
diverged, the hand-run path is the one that rots, because it is the one with no
users - so they are made identical and the possibility is removed. Every
automated run is therefore also a test of the documented one.

The second reason is the original one: forking is what keeps torch out of the
viewer's process. Theatre must stay installable and runnable on a box with no
CUDA, because watching a run costs nothing and that is the whole bargain.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

# The literal command from ms-moe's README. Quoted as data so the test that
# asserts we run exactly this has something to compare against.
MS_MOE_COMMAND = "ms-moe"
BUILD_VERB = "build"


class StagehandUnavailable(RuntimeError):
    """ms-moe is not installed, so there is nothing to fork."""


def resolve_command() -> List[str]:
    """Find `ms-moe`, preferring the literal console script.

    Order matters and the fallback is deliberately second-class:

      1. `ms-moe` on PATH - the documented command, the one in the README, the
         one a person types. This is the path we want every automated run to
         exercise.
      2. `python -m ms_moe` - works, but it is NOT the documented command, so
         using it means the automated run is no longer testing the hand-run
         path. The caller is told when this happens rather than left to assume
         the guarantee still holds.

    Raises StagehandUnavailable if neither exists, because the honest failure
    for a missing optional dependency is a sentence naming the extra - not a
    FileNotFoundError from subprocess three frames down.
    """
    found = shutil.which(MS_MOE_COMMAND)
    if found:
        return [found]

    try:
        import ms_moe  # noqa: F401
    except ImportError:
        raise StagehandUnavailable(
            "ms-moe is not installed. Stagehand is the half of SerenTheatre "
            "that does the work, and it is an opt-in extra:\n"
            "    pip install 'seren-theatre[stagehand]'\n"
            "The viewer works perfectly without it - watching a run has never "
            "required being able to start one."
        ) from None
    return [sys.executable, "-m", "ms_moe"]


def build_argv(recipe: Path, *, json_events: bool = True,
               extra: Sequence[str] = ()) -> List[str]:
    """The exact argv stagehand will exec."""
    argv = resolve_command() + [BUILD_VERB, str(recipe)]
    if json_events:
        argv.append("--json")
    argv.extend(extra)
    return argv


def run(recipe: Path, *, json_events: bool = True,
        extra: Sequence[str] = (), cwd: Optional[Path] = None,
        echo: bool = True) -> int:
    """Fork ms-moe and relay it. Returns the child's exit code.

    Relay, not summarise. Stagehand adds NOTHING to the stream - ms-moe already
    speaks a documented event vocabulary and writes the run manifest that
    Theatre reads. A wrapper that re-interpreted either would become a third
    opinion about what a build is doing, and three opinions is how a dashboard
    starts disagreeing with itself.
    """
    argv = build_argv(recipe, json_events=json_events, extra=extra)
    if echo:
        # On stderr: stdout belongs to the event stream, and a consumer piping
        # this to jq should not have to filter our banner out of it.
        print(f"stagehand → {' '.join(argv)}", file=sys.stderr, flush=True)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(argv, cwd=str(cwd) if cwd else None, env=env)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # Pass the interrupt DOWN rather than dying above a live child. An
        # orphaned training run holding a GPU is a genuinely annoying thing to
        # discover an hour later, and the child has its own cleanup to do.
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 130


def available() -> bool:
    """Is stagehand usable? A read-only question, safe to ask from a GET."""
    try:
        resolve_command()
    except StagehandUnavailable:
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="seren-theatre-stagehand",
        description="Start a Ms.MoE build. The theatre shows the data; the "
                    "stagehand does the work.",
        epilog="Runs `ms-moe build <recipe> --json` - the literal command from "
               "the ms-moe README, so every automated run also tests the "
               "hand-run path.")
    ap.add_argument("recipe", nargs="?", help="path to the recipe .yaml")
    ap.add_argument("--check", action="store_true",
                    help="report whether stagehand is usable, and exit")
    ap.add_argument("--prose", action="store_true",
                    help="let ms-moe print prose instead of JSON events")
    ap.add_argument("--cwd", default=None,
                    help="directory to run the build in (default: the "
                         "recipe's own directory, which is where the pipeline "
                         "and its run roots normally live)")
    ap.add_argument("rest", nargs=argparse.REMAINDER,
                    help="anything after -- is passed straight to ms-moe "
                         "(e.g. -- --dryrun --allow-refusals)")
    a = ap.parse_args(argv)

    if a.check:
        try:
            cmd = resolve_command()
        except StagehandUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 1
        literal = cmd[0].endswith(MS_MOE_COMMAND) or cmd[0].endswith(
            MS_MOE_COMMAND + ".exe")
        print(json.dumps({
            "available": True,
            "command": cmd,
            # Named honestly: on the module fallback the automated run is no
            # longer exercising the documented command, so the "every run
            # tests the hand-run path" guarantee does not hold.
            "is_documented_command": literal,
        }))
        return 0

    if not a.recipe:
        ap.error("a recipe path is required (or use --check)")

    recipe = Path(a.recipe).resolve()
    if not recipe.is_file():
        print(f"recipe not found: {recipe}", file=sys.stderr)
        return 2

    extra = [x for x in a.rest if x != "--"]
    try:
        return run(recipe, json_events=not a.prose, extra=extra,
                   cwd=Path(a.cwd) if a.cwd else recipe.parent)
    except StagehandUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
