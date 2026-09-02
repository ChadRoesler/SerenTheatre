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

Stagehand runs the literal string from ms-moe-maker's README:

    ms-moe-maker build recipe.yaml

Not an import of ms_moe_maker.runner, not a Python API with its own defaults. The
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
import time
from pathlib import Path
from typing import List, Optional, Sequence

# The literal command from ms-moe-maker's README. Quoted as data so the test that
# asserts we run exactly this has something to compare against.
MS_MOE_COMMAND = "ms-moe-maker"
BUILD_VERB = "build"


class StagehandUnavailable(RuntimeError):
    """ms-moe-maker is not installed, so there is nothing to fork."""


# ── which install answers ────────────────────────────────────────────────────
#
# MODULE STATE, ON PURPOSE, AND IT IS THE SAFER OF TWO BAD OPTIONS.
#
# Five places fork the builder: describe, validate, build_argv, run and
# run_detached. They must all reach the SAME binary - this module's own
# docstring warns that a second answer to one question is how a dashboard
# starts disagreeing with itself, and "Backstage described install A while the
# build ran install B" is that failure exactly, with no symptom but a form that
# looks slightly stale.
#
# Threading a config argument through all five means five chances to forget
# one, and the forgotten one degrades silently to PATH. So the configuration is
# set once, here, and `resolve_command` is the only door. Every call site keeps
# its signature and none of them can opt out.
#
# tests/test_stagehand.py asserts the door is the only one - by AST, over this
# package - so a sixth fork added later cannot quietly grow its own resolution.
_PIPELINE = None


def configure(pipeline) -> None:
    """Point every fork at one install. Called by create_app and by the CLI.

    Pass None to reset, which is what a test wants between cases.
    """
    global _PIPELINE
    _PIPELINE = pipeline


def _venv_candidates(venv: Path) -> List[List[str]]:
    """The console script inside a venv, then that venv's own interpreter.

    STILL INSIDE THE VENV NAMED, both times. A venv that has the package but no
    console script - an editable install, a `pip install .` that skipped
    scripts - is a real and recoverable state, and falling back to the venv's
    own `python -m` keeps the promise the setting made. Falling back to the
    SYSTEM python would not: it would run a different install while reporting
    that the config was honoured.
    """
    if os.name == "nt":
        script = venv / "Scripts" / (MS_MOE_COMMAND + ".exe")
        python = venv / "Scripts" / "python.exe"
    else:
        script = venv / "bin" / MS_MOE_COMMAND
        python = venv / "bin" / "python"
    out = []
    if script.is_file():
        out.append([str(script)])
    if python.is_file():
        out.append([str(python), "-m", "ms_moe_maker"])
    return out


class Resolution:
    """What we resolved, where it came from, and why it failed if it did.

    A small object rather than a bare argv because the API reports all three.
    "Which ms-moe-maker is this?" had no answer anywhere in the service, and a
    person debugging a stale craft form had nothing to look at.
    """

    __slots__ = ("argv", "source", "error")

    def __init__(self, argv=None, source="", error=""):
        self.argv = list(argv or [])
        self.source = source
        self.error = error

    def as_dict(self) -> dict:
        return {"command": " ".join(self.argv) if self.argv else "",
                "source": self.source, "error": self.error,
                # The documented command was used, rather than the second-class
                # `-m` form. Reported because an automated run on the fallback
                # is no longer exercising the path a person types.
                "literal": bool(self.argv) and not (
                    len(self.argv) > 1 and self.argv[1] == "-m")}


def resolve() -> Resolution:
    """The single resolution. Never raises; the error is a field.

    Order, and the first three are only reachable when nothing is configured:

      0. `pipeline.command` - an explicit path. Nothing beats being told.
      0b. `pipeline.venv` - the console script in it, else its own python -m.
      1. `ms-moe-maker` on PATH - the documented command, what a person types.
      2. `python -m ms_moe_maker` - works, NOT the documented command, so the
         caller is told rather than left to assume the guarantee holds.

    A CONFIGURED-BUT-MISSING INSTALL IS AN ERROR AND STOPS HERE. Dropping back
    to PATH would be silently doing the exact thing the setting exists to
    prevent, and the person would see a working form describing the wrong box.
    """
    cfg = _PIPELINE
    if cfg is not None and getattr(cfg, "command", ""):
        raw = os.path.expanduser(cfg.command)
        # A PATH SEARCH ONLY FOR A BARE NAME, and the distinction is not
        # pedantry - it was a live hole, caught by the test written to forbid
        # exactly this. `command: ms-moe-maker` means "the one on PATH" and
        # searching is right. `command: /opt/msmoe/bin/ms-moe-maker` names ONE
        # file, and if that file is not there the honest answer is an error:
        # searching PATH would hand back a different install while reporting
        # `pipeline.command`, which is a config being ignored while looking
        # honoured. That is worse than no setting at all.
        named_a_path = os.sep in raw or (os.altsep and os.altsep in raw)
        path = Path(raw)
        if path.is_file():
            return Resolution([str(path)], "pipeline.command")
        if not named_a_path:
            found = shutil.which(raw)
            if found:
                return Resolution([found], "pipeline.command (found on PATH)")
        return Resolution(error=(
            f"pipeline.command is set to {cfg.command!r} and there is no such "
            f"{'file' if named_a_path else 'command'}. Theatre will not fall "
            f"back to PATH here: falling back is the behaviour this setting "
            f"exists to prevent, and it would describe a different install "
            f"than the one you asked for."))

    if cfg is not None and getattr(cfg, "venv", ""):
        venv = Path(os.path.expanduser(cfg.venv))
        candidates = _venv_candidates(venv)
        if candidates:
            argv = candidates[0]
            kind = "console script" if len(argv) == 1 else "python -m"
            return Resolution(argv, f"pipeline.venv ({kind})")
        return Resolution(error=(
            f"pipeline.venv is set to {cfg.venv!r} but there is no "
            f"{MS_MOE_COMMAND} and no python inside it. Expected "
            f"{venv / ('Scripts' if os.name == 'nt' else 'bin')}. Theatre "
            f"will not fall back to PATH: that is what the setting is for."))

    found = shutil.which(MS_MOE_COMMAND)
    if found:
        return Resolution([found], "PATH")

    try:
        import ms_moe_maker  # noqa: F401
    except ImportError:
        return Resolution(error=(
            "ms-moe-maker is not installed. Stagehand is the half of "
            "SerenTheatre that does the work, and it is an opt-in extra:\n"
            "    pip install 'seren-theatre[stagehand]'\n"
            "The viewer works perfectly without it - watching a run has never "
            "required being able to start one.\n"
            "If it IS installed but in another venv, name that venv:\n"
            "    pipeline:\n      venv: /path/to/that/venv"))
    return Resolution([sys.executable, "-m", "ms_moe_maker"],
                      "this interpreter (python -m)")


def resolve_command() -> List[str]:
    """The argv, or StagehandUnavailable. The one door every fork goes through.

    Raises rather than returning empty, because the honest failure for a
    missing optional dependency is a sentence naming the extra - not a
    FileNotFoundError from subprocess three frames down.
    """
    found = resolve()
    if not found.argv:
        raise StagehandUnavailable(found.error)
    return found.argv


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
    """Fork ms-moe-maker and relay it. Returns the child's exit code.

    Relay, not summarise. Stagehand adds NOTHING to the stream - ms-moe-maker already
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
        epilog="Runs `ms-moe-maker build <recipe> --json` - the literal command from "
               "the ms-moe-maker README, so every automated run also tests the "
               "hand-run path.")
    ap.add_argument("recipe", nargs="?", help="path to the recipe .yaml")
    ap.add_argument("--check", action="store_true",
                    help="report whether stagehand is usable, and exit")
    ap.add_argument("--prose", action="store_true",
                    help="let ms-moe-maker print prose instead of JSON events")
    ap.add_argument("--cwd", default=None,
                    help="directory to run the build in (default: the "
                         "recipe's own directory, which is where the pipeline "
                         "and its run roots normally live)")
    ap.add_argument("rest", nargs=argparse.REMAINDER,
                    help="anything after -- is passed straight to ms-moe-maker "
                         "(e.g. -- --dryrun --offline)")
    a = ap.parse_args(argv)

    # SAME RESOLUTION AS THE SERVICE. `--check` exists to answer "will a build
    # work from here", and it would be a poor answer if it consulted a
    # different install than the running Theatre does.
    try:
        from .config import load_config
        configure(load_config().pipeline)
    except Exception:                   # noqa: BLE001 - PATH is a fine default
        pass

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


# ══════════════════════════════════════════════════════════════════════════
#  BACKSTAGE - the half that writes, and the reasons it is allowed to
# ══════════════════════════════════════════════════════════════════════════
#
# Everything above this line is the sibling COMMAND. Everything below is the
# optional ROUTER that seren_theatre.app mounts only when this module imports,
# which happens only when [stagehand] is installed.
#
# That is what turns Theatre's read-only promise from a rule into a property of
# what you installed:
#
#     pip install seren-theatre               -> a viewer. Zero write verbs.
#     pip install seren-theatre[stagehand]    -> a workshop.
#
# tests/test_app.py asserts the first half; the router below is the second.
#
# THE INVARIANT STILL HOLDS, and holds harder than before. Backstage writes
# recipes, and a recipe is an INPUT to a build rather than an artifact of one,
# so it lives in cfg.recipes_dir() - outside every watched stage by
# construction. Every path that reaches disk goes through
# stageguard.assert_outside_stages first, which resolves symlinks and `..` and
# hands back the approved path so a handler cannot check one path and write
# another.
#
# And the run writes its own output, because `--json` already splits it for us:
# "JSON Lines events on stdout, prose on stderr", straight from the builder's
# own --help. So the two files Backstage wants ARE the two streams, and the
# child's own fds are what land in them.
#
# This used to append `--log-file` and `--events-file` to the argv. ms-moe-maker
# has never had either flag. argparse exited 2 in a few milliseconds, the
# streams were DEVNULL so the message went nowhere, nothing wait()ed, and the
# launch was reported as a success. Redirection needs no CLI surface to exist
# and works against every version of the builder, including ones older than any
# flag we might have added.

import shlex
from typing import Any, Dict

DETACHED_MARKER = ".stagehand-run.json"

# THE MARKER IS A WIRE FORMAT, because a reader in another module - and soon
# another person's file - parses it. Same bargain as the run manifest: two
# implementations of one format are fine, two undocumented ones are not. So the
# keys are written down HERE rather than left to be inferred from the dict
# literal below, and a reader can check `schema_version` before believing any
# of it.
#
# WHAT IT IS FOR, precisely, because the temptation is to make it more:
#
#   the marker   says WHAT WAS LAUNCHED and WHETHER THE LAUNCH SUCCEEDED.
#   the manifest says WHAT THE RUN IS DOING, and stays the only authority on it.
#
# `launch == "started"` therefore means "still alive shortly after spawn", NOT
# "alive now". Making it mean the second would be the privileged status channel
# run_detached's docstring refuses, and a reader would then have two opinions
# about a live run - which is how a dashboard starts disagreeing with itself.
#
# `exit_code` is non-null exactly when the child was already gone when we
# looked, which is the only moment this function will ever know one.
MARKER_SCHEMA_VERSION = 1
MARKER_KEYS = ("schema_version", "pid", "argv", "command_line", "cwd",
               "recipe", "started", "log_file", "events_file", "launch",
               "exit_code", "error", "log_tail")

# THE THREE ANSWERS, and the middle one was learned the expensive way.
#
#   "started"   still alive when we looked. The normal case for a build.
#   "finished"  already exited 0. NOT a failure - `build --plan` resolves the
#               config, prints its stages and exits cleanly in about a third of
#               a second. A run can legitimately be shorter than the window.
#   "failed"    already exited non-zero. Died on arrival; nothing is running.
LAUNCH_STARTED = "started"
LAUNCH_FINISHED = "finished"
LAUNCH_FAILED = "failed"

# How long to wait before believing a launch. A build that dies on argv
# parsing dies in milliseconds; a nine-hour run does not finish inside half a
# second. So the timeout FIRING is the success case, and this can never become
# a wait on a real run - it is bounded by construction, not by hope.
LAUNCH_SETTLE_SECONDS = 0.4

# Enough of the log to see an argparse usage message or a traceback's last
# frames, and not so much that a marker becomes a log file.
MARKER_LOG_TAIL_BYTES = 2048


class BuildDiedAtLaunch(RuntimeError):
    """The child exited before it could plausibly have begun building.

    RAISING RATHER THAN RETURNING A FAILED RESULT, and the choice matters. Every
    existing caller treats run_detached's return value as "it started" -
    Backstage hands it straight back as a 200 with a pid in it. A new field on
    that dict only helps a caller who looks at it, and the entire bug this
    replaces was a caller not looking. An exception cannot be not-looked-at.
    """

    def __init__(self, exit_code: int, argv: Sequence[str],
                 log_tail: Optional[str] = None) -> None:
        self.exit_code = exit_code
        self.argv = list(argv)
        self.log_tail = log_tail
        tail = f"\n{log_tail}" if log_tail else ""
        super().__init__(
            f"the build exited with code {exit_code} within "
            f"{LAUNCH_SETTLE_SECONDS}s of launch, so nothing is running. "
            f"The command was:\n    "
            f"{' '.join(shlex.quote(a) for a in self.argv)}{tail}")


def _log_tail(path: Optional[Path],
              limit: int = MARKER_LOG_TAIL_BYTES) -> Optional[str]:
    """The end of the log, for a failure message. Never raises.

    This is the whole reason the streams stopped being DEVNULL: the child had
    already said exactly what was wrong before it died, and we were throwing
    the sentence away and then reporting success.
    """
    if path is None:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - limit))
            return fh.read().decode("utf-8", "replace").strip() or None
    except OSError:
        return None


def _write_marker(cwd: Path, payload: Dict[str, Any]) -> Optional[Path]:
    """Drop the marker beside the run, atomically. Never raises.

    ATOMIC because the reader polls a live directory. A half-written file would
    read as damage, and a reader taught to shrug at damage is a reader that
    shrugs at real damage.

    NEVER RAISES because the marker is a courtesy to whoever is watching, not
    part of the launch. A build that is genuinely running must not be reported
    as failed because the directory went read-only.
    """
    target = Path(cwd) / DETACHED_MARKER
    tmp = target.with_name(f"{DETACHED_MARKER}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return target
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return None


def run_detached(recipe: Path, *, cwd: Path, log_file: Optional[Path] = None,
                 events_file: Optional[Path] = None,
                 extra: Sequence[str] = ()) -> Dict[str, Any]:
    """Start a build that OUTLIVES this process. Returns {pid, argv, ...}.

    A build is hours. Theatre restarts - a config change, a service bounce, an
    upgrade - and a run started from Backstage must not die because the viewer
    that launched it went away. That is the same reasoning as run-msmoe.sh's
    --detach flag, which exists because "don't let that be how a 14B rung ends"
    was written after it nearly was.

    So: new session/process group, and no wait FOR THE RUN. The parent forgets
    the child and learns everything afterwards the same way it learns about a
    run started by hand in a terminal - from the manifest and the log. There is
    deliberately NO privileged channel for Backstage runs, because a second way
    to know what is happening is a second opinion, and two opinions is how a
    dashboard starts disagreeing with itself.

    THE ONE THING IT DOES WAIT FOR IS THE LAUNCH, for LAUNCH_SETTLE_SECONDS.
    That is not a second opinion about the run - it is the difference between
    "started" and "we typed a command". If the child is already gone by then
    with a NON-ZERO code it never started, and this raises BuildDiedAtLaunch
    rather than handing back a pid that belongs to nothing. Gone with code 0 is
    a run that was simply shorter than the window, and is reported as such.

    Writes DETACHED_MARKER into `cwd` either way and returns the same dict; see
    MARKER_KEYS for the shape, which another module parses. Raises OSError if
    the output files cannot be opened, because a build whose output goes
    nowhere is one nobody will be able to see afterwards, and discovering that
    silently is the disease this whole function had.
    """
    argv = list(resolve_command()) + [BUILD_VERB, str(recipe), "--json"]
    argv.extend(extra)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console, and Ctrl-C
        # in whatever launched Theatre does not reach the build.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        # A new session, so closing the SSH connection Theatre was started from
        # does not SIGHUP a nine-hour training run.
        kwargs["start_new_session"] = True

    # FILE HANDLES, NOT PIPES, AND NOT DEVNULL.
    #
    # The old comment here was right about pipes and wrong about the
    # conclusion. A pipe nobody reads DOES fill its buffer and block the child
    # forever - the classic detach bug, and it would look exactly like a
    # training run that hung mid-stage. That is a property of PIPES. A file
    # handle has no such buffer to fill and cannot block the writer, so
    # redirecting to one keeps the anti-blocking property AND keeps the
    # evidence, which DEVNULL threw away along with the argparse error that
    # would have shown this bug on day one.
    #
    # `--json` already splits the two streams the way Backstage wants them:
    # events (JSON Lines) on stdout, prose on stderr. So stdout is the .jsonl
    # and stderr is the .log, and no flag has to exist for it.
    #
    # Append, never truncate: two launches that land in the same second share a
    # filename, and losing the first one's log to the second is a bad trade for
    # a tidier file.
    stdout_handle = None
    stderr_handle = None
    try:
        if events_file is not None:
            stdout_handle = open(events_file, "ab", buffering=0)
        if log_file is not None:
            stderr_handle = open(log_file, "ab", buffering=0)
    except OSError as exc:
        for handle in (stdout_handle, stderr_handle):
            if handle is not None:
                handle.close()
        raise OSError(
            f"refusing to start a build whose output would go nowhere: "
            f"{exc}") from exc

    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle if stdout_handle is not None
            else subprocess.DEVNULL,
            stderr=stderr_handle if stderr_handle is not None
            else subprocess.DEVNULL,
            **kwargs)
    finally:
        # The child holds its own dup of each descriptor from here on, and it
        # holds them for nine hours. The parent letting go is what keeps this
        # honest about who is writing into a stage - the builder is, the same
        # as when a person runs it by hand.
        for handle in (stdout_handle, stderr_handle):
            if handle is not None:
                handle.close()

    marker: Dict[str, Any] = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "pid": proc.pid, "argv": argv,
        "command_line": " ".join(shlex.quote(a) for a in argv),
        "cwd": str(cwd), "recipe": str(recipe), "started": time.time(),
        "log_file": str(log_file) if log_file else None,
        "events_file": str(events_file) if events_file else None,
        "launch": LAUNCH_STARTED, "exit_code": None, "error": None,
        "log_tail": None,
    }

    # "STARTED" HAS TO MEAN STARTED. wait() with a timeout rather than
    # sleep-then-poll, because the timeout expiring is the answer we want and
    # an early exit is reported the instant it happens instead of at the end of
    # a fixed nap.
    try:
        code: Optional[int] = proc.wait(timeout=LAUNCH_SETTLE_SECONDS)
    except subprocess.TimeoutExpired:
        code = None

    # ALREADY GONE IS NOT AUTOMATICALLY A FAILURE, and finding that out cost a
    # real run. `build --plan` does its whole job - resolve the config, print
    # the stage ladder - and exits 0 in about a third of a second. The EXIT
    # CODE is what separates that from the bug this function exists to stop:
    # argparse exits 2 on an unrecognised flag, and no successful build has
    # ever exited non-zero.
    if code == 0:
        marker.update(launch=LAUNCH_FINISHED, exit_code=0)
    elif code is not None:
        tail = _log_tail(log_file)
        marker.update(launch=LAUNCH_FAILED, exit_code=code, log_tail=tail,
                      error=f"exited with code {code} within "
                            f"{LAUNCH_SETTLE_SECONDS}s of launch")
        # The marker is written for the FAILURE too - a reader needs to see
        # that a launch was attempted and died, which is a different fact from
        # no launch at all, and it is the fact the old code hid.
        _write_marker(cwd, marker)
        raise BuildDiedAtLaunch(code, argv, tail)

    _write_marker(cwd, marker)
    return dict(marker)
