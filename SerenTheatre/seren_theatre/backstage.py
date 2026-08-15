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


def _registries() -> Dict[str, Any]:
    """The corpus kinds and validators available on THIS box.

    Backstage's craft form is built from these rather than from a hardcoded
    list in the viewer, so a kind or validator registered by a plugin appears
    in the form without the viewer having heard of it. That is the difference
    between extensible in principle and extensible in fact.
    """
    out: Dict[str, Any] = {"kinds": [], "validators": [], "errors": []}
    try:
        from ms_moe_maker import corpus
        out["kinds"] = corpus.describe()
        out["errors"].extend(corpus.load_errors())
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"corpus registry unavailable: {exc}")
    try:
        from ms_moe_maker import validators
        out["validators"] = validators.describe()
        out["errors"].extend(validators.load_errors())
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"validator registry unavailable: {exc}")
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
