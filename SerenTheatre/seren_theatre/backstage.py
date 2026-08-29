"""Backstage - craft a recipe, save it, hand it to the stagehand.

MOUNTED ONLY WHEN [stagehand] IS INSTALLED. seren_theatre.app imports this
inside a try/except; without the extra there is no router, no write verb, and
no tab. That is the whole shape of the promise:

    pip install seren-theatre             -> a viewer. Zero write verbs.
    pip install seren-theatre[stagehand]  -> a workshop.

WHO OWNS WHAT, because getting this wrong is how the theatre starts doing the
work:

    Backstage  edits and SAVES the recipe card. It never builds anything.
    Stagehand  takes the card, carries it, and starts the builder. The ferryman.
    MsMoEMaker builds. It is fully encapsulated and knows nothing about either.

Backstage does not validate recipes itself, and that is deliberate: it forks
`ms-moe-maker validate`, the literal documented command. A second validator
living in the viewer would drift from the one the builder actually uses, and
the failure mode is the worst available - a recipe that Backstage calls good
and the builder then refuses, an hour into a GPU booking.

EVERY WRITE GOES THROUGH THE GUARD. Recipes live in cfg.recipes_dir(), outside
every watched stage by construction, and stageguard.assert_outside_stages is
still called on each one - construction plus a check, because "it can't happen"
is how it happens.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import stagehand
from .stageguard import WritesIntoStage, assert_outside_stages

# THE GATE, and it has to be this import rather than this module merely
# existing.
#
# seren_theatre/stagehand.py ships in the BASE wheel - the [stagehand] extra
# adds a DEPENDENCY (ms-moe-maker), not a file. So app.py's
# `try: from .backstage import router` always succeeded, and Backstage mounted
# its five write routes on a plain viewer install. The read-only promise was
# being made by a try/except that could not fail.
#
# Importing the builder is what actually distinguishes the two install shapes,
# so it is done at module scope where it can gate the whole router. Nothing
# below needs the symbol; the import IS the assertion.
import ms_moe_maker  # noqa: F401  - imported for its absence, not its contents

# A recipe filename, and nothing else. Not a path - a NAME.
#
# This is the only thing between a POST body and the filesystem, so it is a
# strict allowlist rather than a blocklist: no separators, no dots that could
# start a traversal, no absolutes, no drive letters, no NTFS streams. The guard
# would catch a traversal out of the recipes dir anyway, but a name that never
# contained a separator cannot express one, and two independent defences
# against the oldest bug in web software is not excessive.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_ALLOWED_SUFFIXES = (".yaml", ".yml")
# Recipes are documents, not datasets. A megabyte is already absurd for one.
MAX_RECIPE_BYTES = 512 * 1024


class RecipeBody(BaseModel):
    name: str
    text: str


class RunBody(BaseModel):
    name: str
    dryrun: bool = True
    allow_refusals: bool = False
    # Which stage to build in. Named rather than free-form: the run has to
    # happen somewhere the pipeline lives, and letting a POST choose an
    # arbitrary cwd is a remote-execution primitive with extra steps.
    stage: Optional[str] = None


def _safe_recipe_path(cfg, name: str) -> Path:
    if not _SAFE_NAME.match(name or ""):
        raise HTTPException(
            400, f"{name!r} is not a usable recipe name. Letters, digits, "
                 f"dot, dash and underscore only - it becomes a filename.")
    if not name.endswith(_ALLOWED_SUFFIXES):
        name += ".yaml"
    target = cfg.recipes_dir() / name
    try:
        # Belt AND braces. The name cannot express a traversal and the
        # directory is outside every stage; this proves it rather than
        # assuming it, and returns the path it approved so nothing downstream
        # can substitute a different one.
        return assert_outside_stages(target, cfg)
    except WritesIntoStage as exc:
        raise HTTPException(409, str(exc)) from exc


def _stage_for(cfg, name: Optional[str]):
    stages = list(getattr(cfg, "stages", None) or [])
    if not stages:
        raise HTTPException(
            409, "no stages are configured, so there is nowhere to build. Add "
                 "a stages: entry pointing at the directory that holds your "
                 "pipeline.")
    if name is None:
        return stages[0]
    for stage in stages:
        if stage.name == name:
            return stage
    raise HTTPException(404, f"no stage named {name!r}. Configured: "
                             f"{[s.name for s in stages]}")


DESCRIBE_TIMEOUT = 20.0


def _ask_the_box() -> Optional[Dict[str, Any]]:
    """`ms-moe-maker describe` - the documented way to learn what an install offers.

    FORKED, NOT IMPORTED, for the reason this module already gives about
    validation: a second copy of a fact living in the viewer drifts from the
    one the builder actually uses. This function used to be a hand-picked list
    of `from ms_moe_maker import ...` calls, and it drifted in both directions
    at once - the craft form showed a validator registry that `--describe` did
    not report, and `--describe` reported a reasoning table the craft form
    could not see. Neither side was wrong on its own; there were simply two
    answers to one question.

    Forking also asks the RIGHT install. `import ms_moe_maker` resolves inside
    whatever interpreter Theatre happens to be running under; the console
    script is the one a person types and the one stagehand actually execs for
    validate and run. On a box where those differ - a viewer in one venv, the
    builder in another - importing describes a package nobody is going to
    build with.

    NOT CACHED, deliberately. The whole argument for surfacing the reasoning
    table is that somebody can drop a yaml at ~/.msmoe/reasoning.yaml and carry
    on without waiting for a release; a cache would hand that person a stale
    table and call it the box. The old import path re-read the yaml on every
    call too, so this costs a process where it used to cost a file read, on a
    request that happens when a tab opens rather than in a loop.

    Returns None - never raises - when the command is absent, slow, unhappy or
    unparseable. Every one of those is "the box did not answer", and the caller
    turns it into a visible error rather than a guess.
    """
    try:
        argv = stagehand.resolve_command() + ["describe"]
    except stagehand.StagehandUnavailable:
        return None
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=DESCRIBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


# What the craft form shows about the box, beyond the registries themselves.
# Read off whatever `--describe` returned rather than restated here, so a key
# the CLI learns to report needs no edit in this file.
_BOX_KEYS = ("version", "templates", "tiers", "gates", "eval_modes",
             "commands", "recipe_schema_version")


def _rows(value: Any, key: str = "name") -> List[Dict[str, Any]]:
    """Normalise a registry list that may be rows or may be bare names.

    STRICT ON WHAT WE WRITE, LENIENT ABOUT WHAT WE READ. `describe` reported
    `kinds` as bare strings until it was changed to report rows like
    `validators` does, and a viewer is going to meet installs on both sides of
    that for as long as people pin versions - which they should. Refusing the
    old shape would mean a Theatre upgrade silently emptied the craft form of
    anyone who had not also upgraded the builder.

    A bare name becomes a row with just a name, and the form renders what it
    has. Degrading to less detail is honest; showing nothing is not.
    """
    out: List[Dict[str, Any]] = []
    for item in value or []:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str):
            out.append({key: item})
    return out


def _registries() -> Dict[str, Any]:
    """What THIS box offers - corpus kinds, validators, the reasoning table.

    Backstage's craft form is built from these rather than from a hardcoded
    list in the viewer, so a kind, validator or reasoning family registered on
    someone's machine appears in the form without the viewer having heard of
    it. That is the difference between extensible in principle and extensible
    in fact.

    The reasoning table earns its place here twice over. It is the registry
    users are actively TOLD to extend - a model family shipping a new delimiter
    is meant to be answerable with a yaml on the box, not a release - and it is
    the one whose misconfiguration is silent. An unknown corpus kind refuses
    and an unavailable validator reports `unmeasurable`; a wrong tag style is a
    wrong ANSWER, because the splitter finds no delimiters, eval reports "did
    not reason", and the think block gets scored as though it were the answer.
    """
    out: Dict[str, Any] = {"kinds": [], "validators": [], "reasoning": {},
                           "box": {}, "errors": []}

    box = _ask_the_box()
    if box is None:
        out["errors"].append(
            "could not ask the box: `ms-moe-maker describe` did not answer. "
            "The craft form is showing nothing rather than guessing.")
        return out

    out["kinds"] = _rows(box.get("kinds"))
    out["validators"] = _rows(box.get("validators"))
    out["reasoning"] = box.get("reasoning") or {}
    out["box"] = {k: box[k] for k in _BOX_KEYS if k in box}

    # Problems the box reported about its own registries. `registry_errors`
    # covers the entry-point loaders; the reasoning table carries its own under
    # `warnings`, which predates that key.
    out["errors"].extend(box.get("registry_errors") or [])
    out["errors"].extend(f"reasoning table: {w}"
                         for w in (out["reasoning"].get("warnings") or []))

    # An older writer answers `describe` without the newer keys. Say which ones
    # are missing rather than rendering an empty panel that looks like an empty
    # box - the difference between "this install has no validators" and "this
    # install is too old to say" is the whole point of keeping unmeasurable
    # apart from fail.
    for key in ("validators", "reasoning"):
        if key not in box:
            out["errors"].append(
                f"this ms-moe-maker does not report {key!r} from `describe`; "
                f"upgrade it to see that half of the form.")
    return out


def router() -> APIRouter:
    api = APIRouter(prefix="/api/backstage", tags=["backstage"])

    # ── read ────────────────────────────────────────────────────────────────

    @api.get("")
    def backstage(request: Request) -> dict:
        cfg = request.app.state.cfg
        recipes_dir = cfg.recipes_dir()
        recipes: List[Dict[str, Any]] = []
        try:
            for path in sorted(recipes_dir.glob("*.y*ml")):
                stat = path.stat()
                recipes.append({"name": path.name, "size": stat.st_size,
                                "modified": stat.st_mtime})
        except OSError:
            pass
        return {
            "available": stagehand.available(),
            "recipes_dir": str(recipes_dir),
            "recipes": recipes,
            "stages": [s.name for s in (cfg.stages or [])],
            **_registries(),
        }

    @api.get("/recipes/{name}")
    def read_recipe(name: str, request: Request) -> dict:
        path = _safe_recipe_path(request.app.state.cfg, name)
        if not path.is_file():
            raise HTTPException(404, f"no recipe named {name!r}")
        return {"name": path.name, "text": path.read_text(encoding="utf-8")}

    # ── write - the whole reason this router is optional ────────────────────

    @api.post("/recipes")
    def save_recipe(body: RecipeBody, request: Request) -> dict:
        cfg = request.app.state.cfg
        if len(body.text.encode("utf-8")) > MAX_RECIPE_BYTES:
            raise HTTPException(413, "a recipe is a document, not a dataset")
        path = _safe_recipe_path(cfg, body.name)

        # VALIDATE BEFORE WRITING, with the builder's own validator. A recipe
        # that Backstage called good and the builder then refuses is the worst
        # failure available here, because it is discovered on a booked GPU.
        result = _validate_text(cfg, body.text)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.text, encoding="utf-8")
        return {"saved": path.name, "path": str(path), "validation": result}

    @api.post("/validate")
    def validate(body: RecipeBody, request: Request) -> dict:
        """Check without saving. No write, but it lives here because it forks
        the same command save does and the two must never diverge."""
        return _validate_text(request.app.state.cfg, body.text)

    @api.post("/run")
    def run(body: RunBody, request: Request) -> dict:
        cfg = request.app.state.cfg
        path = _safe_recipe_path(cfg, body.name)
        if not path.is_file():
            raise HTTPException(404, f"no recipe named {body.name!r}")
        stage = _stage_for(cfg, body.stage)
        cwd = stage.resolved()
        if not cwd.is_dir():
            raise HTTPException(409, f"stage {stage.name!r} is not on disk: {cwd}")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        tag = path.stem
        extra: List[str] = []
        if body.dryrun:
            extra.append("--dryrun")
        if body.allow_refusals:
            extra.append("--allow-refusals")

        try:
            # The log lands IN the stage, which is how Theatre can see it - and
            # THE CHILD OPENS IT. Paths in, no file handles here. See the note
            # at the top of stagehand's backstage section.
            started = stagehand.run_detached(
                path, cwd=cwd,
                log_file=cwd / f"msmoe-{tag}-{stamp}.log",
                events_file=cwd / f"msmoe-{tag}-{stamp}.jsonl",
                extra=extra)
        except stagehand.StagehandUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"could not start the build: {exc}") from exc

        started["stage"] = stage.name
        return started

    return api


def _validate_text(cfg, text: str) -> Dict[str, Any]:
    """Fork `ms-moe-maker validate` on the text, in a temp file outside every stage.

    A temp file rather than stdin because the documented command takes a path,
    and the entire point of forking is that the automated path exercises the
    command a person types. Feeding it a different way would quietly stop
    testing the hand-run path, which is the one with no users and therefore the
    one that rots.
    """
    import tempfile
    handle, tmp = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        argv = list(stagehand.resolve_command()) + ["validate", tmp]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
                "output": (proc.stdout or "") + (proc.stderr or ""),
                "command": " ".join(argv[:-1] + ["<recipe>"])}
    except stagehand.StagehandUnavailable as exc:
        return {"ok": False, "exit_code": None, "output": str(exc),
                "command": None}
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None,
                "output": "validate did not finish in 60s", "command": None}
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
