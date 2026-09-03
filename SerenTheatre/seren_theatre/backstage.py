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
    # NO allow_refusals. It used to append `--allow-refusals`, which
    # ms-moe-maker has never had - the same bug as `--log-file`, from the same
    # habit of writing down the flag we wished for. argparse exits 2 on an
    # unknown flag, so ticking that box did not relax anything; it killed the
    # build. Refusals are reported by the builder either way (see levers.py);
    # there is nothing here to opt into.

    # Which stage to build in. Named rather than free-form: the run has to
    # happen somewhere the pipeline lives, and letting a POST choose an
    # arbitrary cwd is a remote-execution primitive with extra steps.
    stage: Optional[str] = None

    # `--force`, AND IT IS NOT A CHECKBOX ON THE FORM. It is here because
    # the builder's resume refusal names it as one of three ways out, and a
    # refusal that names its own fix while the room can perform none of them
    # is a dead end with good manners.
    #
    # But it DISCARDS FINISHED WORK - the refusal exists precisely because
    # stages that already completed would otherwise be inherited, and on a
    # real run that is twenty minutes of abliteration. So the viewer only
    # offers it on the refusal panel, after the diff has been shown; see the
    # note in scripts.js. Hands on the surface: you cannot reach the button
    # without having been handed the thing it destroys.
    #
    # Unlike the `allow_refusals` box this replaced, it is a REAL FLAG -
    # `ms-moe-maker build --force`, asserted against argparse by the
    # builder's own test_refusal_is_data.py.
    force: bool = False


class ExportBody(BaseModel):
    """Make a prompt book out of a recipe on this shelf.

    NOTES ARE TEXT HERE AND A PATH ON THE COMMAND LINE, and that is the one
    place this deliberately differs from `ms-moe-maker bundle`. The CLI takes
    `--notes some.md` because it is run by somebody who has a file; a browser
    has a textarea and no filesystem. So the text is written to a temp file and
    the documented flag is used on it - the fork still runs the real command,
    which is the whole reason for forking.
    """
    name: str
    notes: str = ""
    # OFF BY DEFAULT, exactly as the CLI has it, and for the same reason its
    # help text gives: a synth corpus runs to gigabytes and a surprise 3 GB
    # download is a worse gift than a small one plus a sentence.
    with_data: bool = False
    stage: Optional[str] = None


# A bundle of a recipe with no corpora is kilobytes; --with-data is however big
# the corpora are, and zipping those is real work on a Nano. Long enough that a
# genuine bundle finishes, short enough that a wedged fork does not hold a
# request open until somebody notices.
BUNDLE_TIMEOUT = 900.0
# What `bundle` returns when the STAMPER could not round-trip - the exporter
# loads its own output back and refuses to write a bundle that would rebuild to
# a different fingerprint. Named because Theatre reports it differently from
# every other non-zero: it is a refusal with a diff in it, not a crash.
BUNDLE_DRIFT_EXIT = 2
# Notes are a covering letter, not a chapter.
MAX_NOTES_BYTES = 256 * 1024


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
                           "box": {}, "errors": [], "pipeline": {}}

    # WHICH INSTALL IS ABOUT TO ANSWER. Reported beside the answers rather than
    # somewhere else, because every error below is a statement about a specific
    # binary and was previously attributed to no binary at all - "upgrade it"
    # is not actionable until you know which `it`.
    out["pipeline"] = stagehand.resolve().as_dict()
    if out["pipeline"].get("error"):
        out["errors"].append(out["pipeline"]["error"])

    box = _ask_the_box()
    if box is None:
        where = out["pipeline"].get("command") or "no command resolved"
        out["errors"].append(
            f"could not ask the box: `{where} describe` did not answer "
            f"(missing, too slow, non-zero, or unparseable output). The craft "
            f"form is showing nothing rather than guessing.")
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
                f"the ms-moe-maker at "
                f"{out['pipeline'].get('command') or '(unknown)'} does not "
                f"report {key!r} from `describe`; upgrade THAT install to see "
                f"that half of the form. If it is not the one you develop in, "
                f"set `pipeline.venv` in the config - the answer is coming "
                f"from whichever binary is named there.")
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
        if body.force:
            extra.append("--force")

        try:
            # The log lands IN the stage, which is how Theatre can see it: the
            # child's own stdout and stderr are redirected into these two
            # files, so the BUILDER is the process writing there, the same as
            # when a person runs it by hand. See the note at the top of
            # stagehand's backstage section.
            started = stagehand.run_detached(
                path, cwd=cwd,
                log_file=cwd / f"msmoe-{tag}-{stamp}.log",
                events_file=cwd / f"msmoe-{tag}-{stamp}.jsonl",
                extra=extra)
        except stagehand.StagehandUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except stagehand.BuildDiedAtLaunch as exc:
            # NOT 200-with-a-sad-field, whatever the code. The failure this
            # replaces was a dead build reported as a live one, so the status
            # has to disagree too - a caller that reads only the code must not
            # come away thinking a run is in progress.
            #
            # BUT A REFUSAL IS NOT A SERVER ERROR, and this package already
            # says so out loud: the release workflow accepts exit 1 from a
            # build because "a refusal is a legitimate answer and NOT a CI
            # failure - it means the recipe asks for something the pipeline
            # cannot honour yet, which is the tool working."
            #
            # Reporting that as 500 makes the operator's log say ERROR and the
            # browser say Internal Server Error, both of which point at
            # Theatre. The thing that needs fixing is in the recipe, and it is
            # already in the message. 409 says "this conflicts with what this
            # box can currently do", which is exactly what happened.
            #
            # Anything OTHER than 1 stays a 500: argparse exits 2 on a flag
            # nobody supports, a signal is negative, and those are breakage
            # rather than judgement.
            status = 409 if exc.exit_code == 1 else 500

            # A DICT, NOT A SENTENCE. FastAPI puts `detail` in the body
            # whatever shape it is, and the builder now emits its refusal
            # as data: the finished stages it would inherit, the field diff,
            # and the ways out. Flattening that back to str(exc) here would
            # be the same loss twice - ms-moe-maker had the lists and printed
            # sentences; there is no reason for Theatre to repeat the trick.
            #
            # `text` is always present and always the prose, so a caller
            # that reads nothing else still gets the whole message. `event`
            # is absent when the builder said nothing structured - an older
            # install, or a death before it could emit - and the viewer says
            # so rather than rendering an empty table.
            detail: Dict[str, Any] = {
                "text": str(exc),
                "exit_code": exc.exit_code,
                "command_line": " ".join(exc.argv),
                "log_tail": exc.log_tail,
                "event": exc.event,
            }
            raise HTTPException(status, detail) from exc
        except OSError as exc:
            raise HTTPException(500, f"could not start the build: {exc}") from exc

        started["stage"] = stage.name
        return started

    @api.post("/export")
    def export(body: ExportBody, request: Request) -> dict:
        """A recipe on this shelf -> a prompt book on the shelf next to it.

        THE GAP THIS CLOSES. Backstage could write a recipe and start a build
        from it; the Repertoire could receive a bundle somebody else made and
        hand it back out. What it could not do was make one - so the answer to
        "send me that gauntlet" was still "ssh in and run `ms-moe-maker
        bundle`", from a room whose entire purpose is that you should not have
        to.

        WHY IT LANDS ON THE SHELF RATHER THAN COMING BACK AS A DOWNLOAD.
        Because the shelf already does the rest of the job: it lists what you
        have, shows the recipe and the notes without unpacking, warns when a
        recipe would run somebody's script, keeps the bytes somewhere a
        closed tab cannot lose them, and hands the zip out at
        /api/books/<id>/bundle - the same route, byte for byte, that the
        person you send it to will check against their copy. Returning the file
        directly would mean a second delivery path to keep working, and the one
        that already exists is the better one.

        The book_id IS the content hash, which makes "we are looking at the
        same book" a checkable claim rather than an asserted one. It does NOT
        make exporting twice idempotent, and finding that out cost a paragraph:
        `bundle` stamps the time into the recipe header and into the metadata,
        so an unchanged recipe exported twice is different BYTES. Identity by
        hash is still right; it is simply answering a different question from
        "have I already got this". See _same_book_already for the one that
        keeps the shelf from filling with timestamp-twins.

        FORK, NEVER IMPORT, for the third time in this module and the same
        reason: `bundle` resolves the recipe against THIS box's defaults and
        stamps the answers in. Reimplementing that here would mean a second
        stamper, and the failure mode of a second stamper is a bundle that says
        it rebuilds to a build_id it does not rebuild to - discovered by the
        person you gave it to, on their GPU booking.
        """
        cfg = request.app.state.cfg
        archive = request.app.state.open_archive()
        if archive is None:
            raise HTTPException(
                503, f"the archive is not available, so there is nowhere to "
                     f"put a prompt book: "
                     f"{request.app.state.archive_error or 'disabled'}")

        path = _safe_recipe_path(cfg, body.name)
        if not path.is_file():
            raise HTTPException(404, f"no recipe named {body.name!r}")
        if len(body.notes.encode("utf-8")) > MAX_NOTES_BYTES:
            raise HTTPException(413, "the notes are a covering letter, not a "
                                     "chapter")

        # IN THE STAGE, because that is where a build would run and therefore
        # where the recipe's roots resolve. `bundle --with-data` looks for the
        # corpora under the resolved data_root; run it anywhere else and it
        # honestly reports finding none, which reads as "this recipe has no
        # corpora" rather than as "I looked in the wrong place".
        stage = _stage_for(cfg, body.stage)
        cwd = stage.resolved()
        if not cwd.is_dir():
            raise HTTPException(409, f"stage {stage.name!r} is not on disk: {cwd}")

        import tempfile

        work = Path(tempfile.mkdtemp(prefix="theatre-bundle-"))
        # OUTSIDE EVERY STAGE, proved rather than assumed. mkdtemp lands in the
        # system temp dir and cannot be inside a stage on any sane box - and
        # "cannot happen" is how it happens, so it is checked. The zip is an
        # artifact of the viewer, and a viewer that writes into a watched
        # directory has started doing the work.
        assert_outside_stages(work, cfg)
        out = work / "bundle.zip"
        try:
            argv = list(stagehand.resolve_command()) + [
                "bundle", str(path), "--out", str(out)]
            if body.with_data:
                argv.append("--with-data")
            if body.notes.strip():
                notes_file = work / "notes.md"
                notes_file.write_text(body.notes, encoding="utf-8")
                argv += ["--notes", str(notes_file)]

            try:
                proc = subprocess.run(argv, cwd=str(cwd), capture_output=True,
                                      text=True, timeout=BUNDLE_TIMEOUT)
            except stagehand.StagehandUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            except subprocess.TimeoutExpired as exc:
                raise HTTPException(
                    504, f"`bundle` did not finish in {BUNDLE_TIMEOUT:.0f}s. "
                         f"With --with-data that can mean the corpora are "
                         f"very large rather than that anything is wrong."
                    ) from exc

            said = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                # A DRIFT REFUSAL IS NOT A SERVER ERROR, the same argument the
                # run route makes about a resume refusal. The exporter loaded
                # its own output back, resolved it, found a field that did not
                # survive the round trip, and declined to write a bundle that
                # would build something else. That is the tool working, and it
                # names the fields. 409 says "this conflicts with what this box
                # can currently do"; 500 would point at Theatre.
                status = 409 if proc.returncode == BUNDLE_DRIFT_EXIT else 500
                raise HTTPException(status, {
                    "text": said.strip() or f"`bundle` exited "
                                            f"{proc.returncode} and said "
                                            f"nothing",
                    "exit_code": proc.returncode,
                    "command_line": " ".join(argv),
                    "stamper_drift": proc.returncode == BUNDLE_DRIFT_EXIT,
                })
            if not out.is_file():
                # SAID IT WORKED AND WROTE NOTHING. Reported rather than
                # turned into a 500 further down, because the difference
                # between "the fork failed" and "the fork lied" is the whole
                # first half of an hour of debugging.
                raise HTTPException(500, {
                    "text": f"`bundle` exited 0 and wrote no file at {out}. "
                            f"What it said:\n\n{said.strip()}",
                    "exit_code": 0, "command_line": " ".join(argv),
                    "stamper_drift": False})

            shelved = _shelve(cfg, archive, out,
                              name=body.name.rsplit(".", 1)[0], reuse=True)
        finally:
            # The zip is on the shelf by now, content-addressed. This directory
            # existing afterwards would be a second copy nothing points at.
            import shutil as _shutil
            _shutil.rmtree(work, ignore_errors=True)

        shelved["exported"] = True
        shelved["with_data"] = bool(body.with_data)
        shelved["stage"] = stage.name
        # THE COMMAND'S OWN OUTPUT, kept. It prints the sixteen fields that
        # cannot travel in a recipe and their values on THIS box, and the size
        # of the corpora it did not include. Both are things the person doing
        # the sending needs to read once, and neither is anywhere else.
        shelved["output"] = said.strip()
        shelved["command_line"] = " ".join(argv)
        return shelved

    # ── the repertoire: prompt books ────────────────────────────────────────
    #
    # A PROMPT BOOK is a recipe bundle somebody can stage again - the stage
    # manager's annotated master copy, which is the artifact that exists so
    # another company can restage the show. `ms-moe-maker bundle` makes them.
    #
    # WHY IMPORT LIVES BEHIND [stagehand] AND LISTING DOES NOT. These are
    # writes, and a base install must keep exposing zero verbs that change
    # anything. That is not a hardship for the sharing story: to build from a
    # book you need ms-moe-maker, so anyone importing one has the workshop
    # already. Reading the shelf - what is on it, what a recipe says, handing
    # the zip back on - are read routes in app.py, available to a plain viewer.
    #
    # RAW BODY, NOT multipart. A file upload would mean adding python-multipart
    # to a package whose entire dependency list is four things, for a POST that
    # `fetch(url, {method: 'POST', body: file})` already does perfectly well.

    @api.post("/books")
    async def import_book(request: Request, name: str = "") -> dict:
        """Take in a bundle. Store the bytes; keep the claim it makes.

        VALIDATION IS BEST-EFFORT AND NEVER A REFUSAL, and that is the whole
        sharing story rather than a leniency. The box a bundle is given to is
        exactly the one whose ms-moe-maker might be older, or differently
        configured, or absent from PATH - refusing the gift on the machine it
        was given to would make the feature fail at precisely its purpose. So
        it is stored either way and marked with what could not be checked.
        """
        cfg = request.app.state.cfg
        archive = request.app.state.open_archive()
        if archive is None:
            raise HTTPException(
                503, f"the archive is not available, so there is nowhere to "
                     f"put a prompt book: "
                     f"{request.app.state.archive_error or 'disabled'}")

        raw = await request.body()
        if not raw:
            raise HTTPException(400, "no bundle in the request body")

        import tempfile

        handle, tmp = tempfile.mkstemp(suffix=".zip")
        os.close(handle)
        tmp_path = Path(tmp)
        try:
            tmp_path.write_bytes(raw)
            return _shelve(cfg, archive, tmp_path, name=name)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    @api.delete("/books/{book_id}")
    def delete_book(book_id: str, request: Request) -> dict:
        """Remove a book, and its bytes only if nothing else names them.

        DEDUP CUTS BOTH WAYS and this is where it bites. Two prompt books that
        share a corpus share the blob, because identical bytes have identical
        names - so deleting one book must not delete bytes the other still
        points at. The refcount lives in the rows, which is why the store's
        own `delete` takes an instruction rather than a decision.
        """
        archive = request.app.state.open_archive()
        if archive is None:
            raise HTTPException(503, "the archive is not available")
        from .archive import blobs as _blobs

        gone = archive.delete_prompt_book(book_id)
        if not gone:
            raise HTTPException(404, f"no prompt book {book_id!r}")

        still_named = archive.prompt_book_ids()
        freed = False
        if book_id not in still_named:
            cfg = request.app.state.cfg
            store = _blobs.Blobs(cfg.archive.blobs_dir())
            try:
                freed = store.delete(book_id)
            except _blobs.BlobError:
                # The row is gone either way. A blob that will not delete is an
                # orphan, which `orphans()` reports and nothing sweeps.
                freed = False
        return {"deleted": book_id, "bytes_freed": freed}

    return api


def _same_book_already(cfg, archive, meta: Dict[str, Any], notes: str,
                       name: str, data_experts) -> Optional[Dict[str, Any]]:
    """A row that is this bundle in every way that means anything.

    THE PROBLEM, FOUND BY EXPORTING TWICE. book_id is the content hash, which
    is the right answer to "did my friend get the same file I did" and the
    wrong one to "have I already exported this". `bundle` stamps the time into
    the recipe header and into meta.created, so two exports of an UNCHANGED
    recipe are different bytes, different hash, two rows - and a shelf fills up
    with timestamp-twins in about four clicks.

    So identity stays the hash, and this answers the other question honestly:
    same build_id, same notes, same name, same corpora. Everything that
    describes what the bundle IS, minus when it was made. If one matches, the
    new zip is dropped and the existing row is handed back - which also means
    the file you already sent somebody stays the file on the shelf.

    A bundle with NO build_id never matches. It makes no claim, so there is
    nothing to say two of them are the same.

    THE CORPORA ARE COMPARED FROM THE STORED ZIP, not from the row. Which
    corpora a bundle carries is not a column - and it must be part of this,
    because `--with-data` and a plain export of the same recipe share a
    build_id, share notes, share a name, and are the two things you would least
    like handed to you in place of each other. So a candidate that has passed
    every cheap check gets its blob opened and asked. That reads one zip, and
    only for a row that already matched on three fields, which is nearly never
    more than one row.

    A candidate whose bytes are missing does not match. Unknown is not equal;
    failing toward "make a new one" costs a duplicate row and failing the other
    way hands somebody the wrong file.
    """
    build_id = str(meta.get("build_id") or "")
    if not build_id:
        return None
    from .archive import blobs as _blobs
    from .archive import bundle as _bundle
    want = sorted(data_experts or [])
    store = _blobs.Blobs(cfg.archive.blobs_dir())
    for row in archive.prompt_books():
        if str(row.get("build_id") or "") != build_id:
            continue
        if (row.get("notes") or "") != (notes or ""):
            continue
        if name and (row.get("name") or "") != name:
            continue
        try:
            theirs = _bundle.read(store.path_for(row["book_id"]))
        except (_blobs.BlobError, _bundle.UnreadableBundle, OSError):
            continue
        if sorted(theirs["data_experts"]) != want:
            continue
        return row
    return None


def _shelve(cfg, archive, zip_path: Path, name: str = "",
            reuse: bool = False) -> Dict[str, Any]:
    """Put a bundle on the shelf. THE ONE PATH, for both ways one arrives.

    A bundle reaches this box two ways - somebody uploads a zip they were
    given, or Backstage exports one from a recipe here - and everything after
    "there is a zip" is identical: read it, refuse it if it is not safe to
    open, weigh it, store the bytes, write the row. Two copies of that would
    drift, and they would drift in the direction that matters: the import path
    checks the zip for absolute paths, `..` and symlinks, and a second copy
    that forgot one of those is a security check with a hole in it.

    So export does not get its own storing code. It gets this one.
    """
    from .archive import blobs as _blobs
    from .archive import bundle as _bundle

    try:
        got = _bundle.read(zip_path)
    except _bundle.UnreadableBundle as exc:
        # THE ONLY REFUSAL ON CONTENT. Not "this recipe is wrong" - that is a
        # judgement somebody else's box is entitled to disagree with - but
        # "this archive is not safe to open", which is not a matter of opinion.
        raise HTTPException(400, str(exc)) from exc

    # THE CEILING, CHECKED BEFORE THE COPY rather than after. The blob store is
    # designed around small documents; a --with-data bundle is the one thing
    # that can put gigabytes in it. Refusing by name and size is a fine answer.
    # Filling the disk of a box that is nine hours into a build is not.
    ceiling = cfg.archive.max_bundle_bytes()
    size = int(got["bytes"])
    if ceiling and size > ceiling:
        raise HTTPException(
            413, f"this bundle is {size / 1e6:.0f} MB and archive."
                 f"max_bundle_mb is {cfg.archive.max_bundle_mb}. Raise it, or "
                 f"set it to 0 for no ceiling, or bundle without the corpora.")

    meta = got["meta"] or {}

    # BEFORE THE COPY, so an export that changes nothing does not put a second
    # near-identical zip in the store. Only export asks for this: a re-uploaded
    # identical file already collapses to one row by hash, and an upload that
    # differs is somebody else's bundle and gets its own row whatever it
    # resembles.
    if reuse:
        same = _same_book_already(cfg, archive, meta, got["notes"] or "",
                                  name, got["data_experts"])
        if same is not None:
            return {"book_id": same["book_id"],
                    "name": same.get("name") or name,
                    "bytes": int(same.get("bytes") or 0),
                    "data_experts": got["data_experts"],
                    "meta_error": got["meta_error"],
                    "executes": got["executes"],
                    "reused": True,
                    "validated": _validate_text(cfg, got["recipe"])}

    store = _blobs.Blobs(assert_outside_stages(cfg.archive.blobs_dir(), cfg))
    digest = store.put(zip_path)
    # RE-SHELVING MUST NOT RENAME WHAT IS ALREADY THERE. The id is the content
    # hash, so the same zip twice is one row - and the upsert would otherwise
    # reset a name somebody chose back to whatever the bundle calls itself. An
    # explicit name still wins, because that is somebody saying it on purpose.
    existing = archive.prompt_book(digest)
    kept_name = (existing or {}).get("name") or ""
    book = {
        # THE CONTENT HASH IS THE ID. The same bundle twice is one row, not
        # two, and it is the same row your friend has - which makes "we are
        # looking at the same book" checkable rather than asserted. It also
        # means exporting an unchanged recipe twice does not litter the shelf.
        "book_id": digest,
        "name": str(name or kept_name or meta.get("name") or "untitled"),
        "created": meta.get("created"),
        "imported": time.time(),
        "build_id": str(meta.get("build_id") or ""),
        "bytes": size,
        # EXTRACTED FOR VIEWING, per the shape of the thing: you should be
        # able to read what you were given without unpacking it.
        "recipe": got["recipe"],
        "notes": got["notes"],
        "meta": json.dumps(meta, sort_keys=True),
    }
    archive.put_prompt_book(book)

    return {"book_id": digest, "name": book["name"], "bytes": size,
            "data_experts": got["data_experts"],
            "meta_error": got["meta_error"], "reused": False,
            # IN FRONT OF A PERSON, not in a log. A recipe naming an
            # eval.script runs that script with the interpreter.
            "executes": got["executes"],
            "validated": _validate_text(cfg, got["recipe"])}


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
