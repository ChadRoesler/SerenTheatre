"""FastAPI app for SerenTheatre.

Endpoints:
    GET /            - service info
    GET /health      - liveness probe
    GET /api/state   - the whole board, as JSON
    GET /viewer      - the room itself; read-only, auto-refreshing

TWO CHANNELS, KEPT SEPARATE - the Symposium contract.
DATA flows Symposium -> Lodestar -> observatories. EYES go direct to each
service's /viewer, read-only. Theatre is all eyes: it has no write path at all,
which is why it can be pointed at a live 14B run without anyone having to think
about whether it might touch something.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

# Hard imports, not guarded ones. Both are core dependencies and config.py
# already imports Meninges at module scope for ServerConfig, so a guard here
# would only ever hide the second symptom of a failure that already happened.
from seren_meninges.auth import bearer_auth_middleware
from seren_sinew.request_log import RequestLoggingMiddleware

from ._diag import diag
from .config import DEFAULT_PORT, TheatreConfig, load_config
from .sources import scan_stage
from . import __version__ as _fallback_version

# The viewer pack: five files the shell assembles into a page. Resolved at
# import so a missing pack is a startup-time discovery, not a 500 the first
# time somebody opens the page.
_VIEWER_DIR = Path(__file__).resolve().parent / "viewer" / "ui"

# Re-exported from config, NOT redeclared. It was written out twice - once
# here, once there - which is two places to edit and one place to forget. The
# port is a config fact; this module just passes it along for anything
# importing it from here.
__all__ = ["ACCENT", "DEFAULT_PORT", "SERVICE", "create_app"]

# Every other service in the constellation gets a colour. The theatre gets the
# house lights down.
#
# ONE CONSTANT, TWO CONSUMERS, ON PURPOSE. This value is the service's identity
# in GET / (which Lodestar and Symposium read) AND the accent handed to the
# viewer shell. They were previously two different literals - #0a0a0a in the
# installer's --describe, #0a0a0a hardcoded at the render_from_dir call - so
# the colour on a service card and the colour on its own page disagreed, and
# nothing anywhere compared them. Naming it once removes the possibility.
#
# It also stops being a NameError. Removing _describe.py took the definition
# with it and left the reference on the SERVICE line below, so `import
# seren_theatre.app` raised and every entry point into this package was dead.
ACCENT = "#121212"

try:                                    # meninges is the family's shell
    from seren_meninges.viewer import render_from_dir
except Exception:                       # noqa: BLE001 - dev checkout
    render_from_dir = None              # type: ignore[assignment]

try:
    from seren_meninges.updates import updates_payload
except Exception:                       # noqa: BLE001 - dev checkout
    updates_payload = None              # type: ignore[assignment]

# The updates block's key set, stated ONCE. Every install shape's CI asserts
# this exact set on GET /, so a fallback that emitted a different shape would
# turn a missing dependency into a key-drift failure three steps away from the
# cause. tests/test_updates_contract.py pins this against what seren_meninges
# actually produces whenever meninges is importable.
UPDATES_KEYS = ("status", "distribution", "installed", "latest",
                "update_available", "detail", "checked_at")

try:
    from seren_meninges import get_version
    APP_VERSION = get_version("seren-theatre", fallback=_fallback_version)
except Exception:                       # noqa: BLE001 - source checkout
    APP_VERSION = _fallback_version

SERVICE = {"name": "seren-theatre", "group": "auxiliary", "accent": ACCENT,
           "description": "Watch a model being made. Read-only viewer over "
                          "training logs and artifacts."}


async def _updates_block(app: FastAPI) -> dict:
    """The updates block, in EVERY shape, including a broken one.

    GET / must always carry a well-formed updates dict - that is a family
    contract and CI asserts the key set on every install shape. So when the
    checker could not be built, this reports status="unavailable" WITH the full
    key set and a detail saying how to fix it, rather than omitting the block
    and letting a consumer discover the hole by KeyError.

    "unavailable" is deliberately distinct from "you are current". Reporting a
    failed check as good news is the one answer this must never give.
    """
    checker = getattr(app.state, "updates", None)
    if updates_payload is not None:
        return await updates_payload(checker, distribution="seren-theatre",
                                     installed=APP_VERSION)
    return {"status": "unavailable", "distribution": "seren-theatre",
            "installed": APP_VERSION, "latest": None,
            "update_available": False,
            "detail": "seren-meninges is not importable, so update checking "
                      "could not be constructed. It is a core dependency: "
                      "pip install -U seren-theatre",
            "checked_at": None}


def create_app(config: Optional[TheatreConfig] = None) -> FastAPI:
    cfg = config or load_config()
    app = FastAPI(title="SerenTheatre", version=APP_VERSION)
    app.state.cfg = cfg

    # -- Bearer auth --
    # Empty token = open, which is the default and the right default: on
    # loopback, in front of a read-only viewer, a mandatory secret would be
    # ceremony. It exists because the moment somebody widens the bind - and
    # they will, that is how you reach it from your desk - the alternative to
    # a token is publishing training logs to the LAN with nothing in front of
    # them. The knob has to already be there when that day comes.
    app.add_middleware(bearer_auth_middleware(cfg.server.resolve_bearer()))

    # -- Request logging --
    # Theatre is a TOOL other people install, not a private notebook, and this
    # is where those two shapes visibly part company. Margin has no request log
    # on purpose: it is the one surface whose design rests on nobody reading
    # it, so a log of every read would be footprints across exactly the thing
    # being promised. Theatre has the opposite job - when a stranger's viewer
    # is showing them the wrong thing, the log is how they find out why.
    app.add_middleware(
        RequestLoggingMiddleware,
        service_name="seren-theatre",
        env_prefix="SEREN_THEATRE",
    )

    # Catch EVERYTHING, not just ImportError. This feature only draws a badge,
    # and seren_meninges states the contract outright: a version read must
    # never crash startup. A too-narrow catch already bit the family once - a
    # missing cfg.updates raised AttributeError, sailed past `except
    # ImportError`, and took five services down over a cosmetic check.
    try:
        from seren_meninges.updates import UpdateChecker
        app.state.updates = UpdateChecker(
            "seren-theatre",
            enabled=cfg.updates.enabled,
            index_url=cfg.updates.index_url,
            ttl_seconds=cfg.updates.check_interval_hours * 3600.0,
            allow_prerelease=cfg.updates.allow_prerelease,
            fallback_version=APP_VERSION,
        )
    except Exception as exc:            # noqa: BLE001 - cosmetic, never fatal
        app.state.updates = None
        diag(f"[seren-theatre] update checking unavailable ({exc})")

    # -- Backstage, if and only if [stagehand] is installed --
    #
    # THIS try/except IS THE READ-ONLY GUARANTEE. Not a config flag, not a
    # feature toggle - a plain install cannot import this module, so the write
    # verbs do not exist to be turned on. `pip install seren-theatre` is a
    # viewer and tests/test_app.py asserts it has zero mutating routes;
    # `[stagehand]` is a workshop. The install shape IS the promise.
    #
    # Catching Exception rather than ImportError on purpose: a half-installed
    # or version-skewed extra must leave a working VIEWER behind, not take the
    # whole service down. Watching a run has never required being able to
    # start one, and that stays true when the starting half is broken.
    # POINT EVERY FORK AT ONE INSTALL, BEFORE ANYTHING CAN FORK.
    #
    # Before this line, which ms-moe-maker Theatre talked to was a property of
    # the SHELL THE SERVICE WAS STARTED FROM - `shutil.which`, resolved at the
    # moment of the first call. Start it from a login shell carrying an old
    # install on PATH and Backstage describes that one while your work happens
    # in another venv, and the only symptom is a craft form that looks slightly
    # out of date. See PipelineConfig.
    #
    # Imported here rather than at module scope because
    # test_the_viewer_never_imports_stagehand pins that the viewer's import
    # graph stays clean, and configuring is not importing on a viewer's behalf
    # - it is one function call the moment an app is built.
    try:
        from . import stagehand as _stagehand
        _stagehand.configure(getattr(cfg, "pipeline", None))
    except Exception as exc:            # noqa: BLE001 - viewer must survive
        diag(f"[seren-theatre] could not set the pipeline command ({exc})")

    app.state.backstage = False
    try:
        from .backstage import router as _backstage_router
        app.include_router(_backstage_router())
        app.state.backstage = True
        diag("[seren-theatre] backstage mounted ([stagehand] is installed)")
    except Exception as exc:            # noqa: BLE001 - viewer must survive
        diag(f"[seren-theatre] backstage not mounted ({exc})")

    def _pipeline_block() -> dict:
        """The resolution, or why there isn't one. Never raises."""
        try:
            from .stagehand import resolve as _resolve
            return _resolve().as_dict()
        except Exception as exc:        # noqa: BLE001
            return {"command": "", "source": "", "error": str(exc),
                    "literal": False}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": SERVICE["name"], "version": APP_VERSION}

    @app.get("/")
    async def root() -> dict:
        # `stagehand` reports whether the [stagehand] extra is usable on this
        # box. Reporting it is a GET and stays one: the service can SAY the
        # stagehand exists and can never USE it. That asymmetry is the design,
        # not a limitation - if the theatre could start the build, the theatre
        # would be doing the work.
        #
        # Imported lazily so the module is not on the viewer's import graph.
        # test_the_viewer_never_imports_stagehand pins that.
        try:
            from .stagehand import available as _stagehand_available
            stagehand = _stagehand_available()
        except Exception:       # noqa: BLE001 - never let a capability probe 500
            stagehand = False
        from .stageguard import mutating_routes
        return {**SERVICE, "version": APP_VERSION,
                "stages": [s.name for s in cfg.stages],
                "stagehand": stagehand,
                # Whether the optional write half is mounted, and EXACTLY what
                # it added. A person who installed [stagehand] should be able
                # to see the write surface from outside without reading the
                # source; a person who did not should be able to prove the
                # list is empty.
                "backstage": bool(getattr(app.state, "backstage", False)),
                # WHICH BUILDER, on the root document, where a person looks
                # first. It was unanswerable from outside the process: two
                # installs, one of them stale, and no way to tell which one
                # replied. `source` says how it was chosen so a config that is
                # being ignored says so instead of looking honoured.
                "pipeline": _pipeline_block(),
                "write_routes": [p for p, _ in mutating_routes(app)],
                "updates": await _updates_block(app),
                "viewer": "/viewer", "state": "/api/state"}

    # ── the archive ──────────────────────────────────────────────────────
    #
    # OPENED LAZILY AND NEVER FATALLY. A viewer whose whole job is to survive
    # the mess must not be the thing that fails to start because a database
    # file is on a full disk. So the handle is built on first use, the error is
    # kept, and it is REPORTED in the payload rather than raised - the same
    # bargain every other reading in this service makes.
    app.state.archive = None
    app.state.archive_error = ""

    def _archive():
        """The open archive, or None. Tries once; remembers the failure."""
        if app.state.archive is not None or app.state.archive_error:
            return app.state.archive
        if not getattr(cfg, "archive", None) or not cfg.archive.enabled:
            app.state.archive_error = "archive: disabled in config"
            return None
        try:
            from .archive.store import connect
            # `cfg` is passed so the stage guard applies: an archive written
            # inside a watched directory would be deleted by exactly the
            # cleanup this feature exists to survive, and would make the
            # viewer a participant in what it is watching.
            app.state.archive = connect(cfg.archive.resolved_dsn(), cfg)
            diag(f"[seren-theatre] archive: {app.state.archive.path}")
        except Exception as exc:        # noqa: BLE001 - a viewer must survive
            app.state.archive_error = f"archive unavailable: {exc}"
            diag(f"[seren-theatre] {app.state.archive_error}")
        return app.state.archive

    def _harvest(stages) -> dict:
        """Copy finished runs into the archive. A READ that writes, on purpose.

        Two invariants are NOT broken by this and the distinction is worth
        being precise about, because "the viewer writes now" sounds like the
        end of the read-only promise and is not:

          * No mutating ROUTE is added. A base install still exposes zero
            verbs that change anything, which is what test_app asserts.
          * Nothing is written INSIDE A STAGE. stageguard resolved the archive
            path when it was opened; the thing Theatre must never do is
            perturb what it is watching, and this writes somewhere else.

        Harvest lives on the scan rather than a timer or a button because the
        scan is the moment the readings already exist - a second path to the
        same directory would be a second opinion about what is in it, and this
        codebase has paid for that mistake before. It is idempotent, so a poll
        that finds nothing new does nothing at all.
        """
        archive = _archive()
        if archive is None:
            return {"enabled": False, "error": app.state.archive_error}
        stored = 0
        error = ""
        try:
            from .archive import harvest as _harvest_mod
            for stage in stages:
                stored += _harvest_mod.harvest_stage(archive, stage)
        except Exception as exc:        # noqa: BLE001
            # A HARVEST THAT FAILS MUST NOT COST YOU THE DASHBOARD. Watching a
            # run has never required being able to archive one, and a full disk
            # is exactly when you most want to still be able to look.
            error = f"harvest failed: {exc}"
            diag(f"[seren-theatre] {error}")
        out = {"enabled": True, "path": str(archive.path), "error": error}
        try:
            out["surgeries"] = archive.count("surgeries")
            out["stored_now"] = stored
        except Exception as exc:        # noqa: BLE001
            out["error"] = out["error"] or f"archive unreadable: {exc}"
        return out

    @app.get("/api/state")
    def state() -> dict:
        t0 = time.time()
        stages = [scan_stage(s.name, s.resolved(), s.logs, s.rungs,
                             cfg.tail_bytes)
                  for s in cfg.stages]
        return {"generated": t0, "took_ms": round((time.time() - t0) * 1000, 1),
                "refresh_seconds": cfg.refresh_seconds, "stages": stages,
                # Reported, never silent. An archive that is off, broken or
                # full has to say so on the page - a history that quietly
                # stopped being kept is worse than one that was never started,
                # because you find out when you go looking for it.
                "archive": _harvest(stages),
                "version": APP_VERSION}

    # Backstage needs the same handle, and must not open a second one: two
    # connections to one sqlite file is how a writer and a reader start
    # blocking each other on a box with no WAL support.
    app.state.open_archive = _archive

    @app.get("/api/books")
    def books_index() -> dict:
        """The repertoire. A READ route, so a plain viewer has it.

        Import and delete live behind [stagehand] because they write - but
        looking at the shelf, reading what a book says and handing the zip on
        are things anybody watching this box should be able to do.
        """
        archive = _archive()
        if archive is None:
            return {"enabled": False, "error": app.state.archive_error,
                    "books": []}
        try:
            return {"enabled": True, "error": "",
                    "books": archive.prompt_books()}
        except Exception as exc:        # noqa: BLE001
            return {"enabled": True, "error": f"archive unreadable: {exc}",
                    "books": []}

    @app.get("/api/books/{book_id}")
    def book_detail(book_id: str) -> dict:
        """One book, with its recipe and whatever it warns about.

        `executes` is recomputed from the STORED RECIPE rather than read back
        off the row, so a viewer that learns about a new executable field
        starts warning about books imported before it knew.
        """
        archive = _archive()
        if archive is None:
            raise HTTPException(503, app.state.archive_error or "no archive")
        row = archive.prompt_book(book_id)
        if row is None:
            raise HTTPException(404, f"no prompt book {book_id!r}")
        from .archive import bundle as _bundle
        row["executes"] = _bundle.executes(row.get("recipe") or "")
        from .archive import blobs as _blobs
        store = _blobs.Blobs(cfg.archive.blobs_dir())
        # PRESENCE, NOT PROMISES, applied to our own blob store. A row whose
        # bytes are missing is a book you cannot hand on, and saying so is
        # better than a download that 404s later.
        row["bytes_present"] = store.has(book_id)
        return row

    @app.get("/api/books/{book_id}/bundle")
    def book_bundle(book_id: str):
        """Hand the zip back out, unchanged, so it can be passed along.

        The bytes are returned verbatim - same file, same hash, so the person
        you send it to can check it against the one you were given.
        """
        archive = _archive()
        if archive is None:
            raise HTTPException(503, app.state.archive_error or "no archive")
        if archive.prompt_book(book_id) is None:
            raise HTTPException(404, f"no prompt book {book_id!r}")
        from .archive import blobs as _blobs
        store = _blobs.Blobs(cfg.archive.blobs_dir())
        try:
            path = store.path_for(book_id)
        except _blobs.BlobError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not path.is_file():
            raise HTTPException(
                410, f"the row for {book_id!r} is here but its bytes are not. "
                     f"The recipe and the notes are still readable at "
                     f"/api/books/{book_id}.")
        from fastapi.responses import FileResponse
        row = archive.prompt_book(book_id)
        name = "".join(c for c in str(row.get("name") or "bundle")
                       if c.isalnum() or c in "._-") or "bundle"
        return FileResponse(path, media_type="application/zip",
                            filename=f"{name}.zip")

    @app.get("/api/archive")
    def archive_index(limit: int = 50, offset: int = 0) -> dict:
        """Previous Surgeries: runs that happened, with their gradings nested.

        A READ ROUTE, so it exists on a base install - which is the point. The
        person who most needs the history is the one running a plain viewer
        against a directory somebody else built in.

        NESTED RATHER THAN FLAT: a grading is meaningless without the build it
        graded, and a list of scores with no builds beside them is exactly the
        shape that invites the wrong conclusion.
        """
        archive = _archive()
        if archive is None:
            return {"enabled": False, "error": app.state.archive_error,
                    "surgeries": []}
        try:
            from .archive import harvest as _harvest_mod
            rows = archive.surgeries(limit=max(1, min(limit, 500)),
                                     offset=max(0, offset))
            # STAT THEM NOW. A stored `rung_present` is a last-known value, and
            # the row that matters most - the one whose rung was deleted - is
            # exactly the one no later harvest will ever revisit. See reconcile.
            rows = _harvest_mod.reconcile(archive, rows)
            # RE-PROJECT THE STORED DOCUMENTS. The rows carry the ORIGINAL
            # eval and gate reports; the shape the viewer draws is computed
            # here, from them, on every read. That is what makes a viewer
            # upgrade improve rows archived years ago instead of leaving them
            # frozen in whatever projection was current when they were stored.
            from . import evalreport as _proj
            for row in rows:
                manifest = row.get("manifest") or {}
                build = str(manifest.get("build_id") or "")
                for grading in row.get("gradings") or []:
                    doc = grading.get("report")
                    if isinstance(doc, dict):
                        view = _proj.project_eval(doc, build)
                        # The provenance COLUMN wins: it was decided at harvest
                        # against the manifest that was on disk then, and that
                        # is the only moment the question had one honest
                        # answer. Re-deriving it now would compare against a
                        # rung that may since have been rebuilt.
                        view["provenance"] = grading.get("provenance") \
                            or view["provenance"]
                        grading["view"] = view
                gate = (row.get("gate") or {}).get("report")
                if isinstance(gate, dict):
                    row["gate"]["view"] = _proj.project_gate(gate)
            return {"enabled": True, "path": str(archive.path),
                    "total": archive.count("surgeries"), "error": "",
                    "surgeries": rows}
        except Exception as exc:        # noqa: BLE001
            return {"enabled": True, "error": f"archive unreadable: {exc}",
                    "surgeries": []}

    @app.get("/api/archive/diff")
    def archive_diff(a: str, b: str) -> dict:
        """Two runs side by side, with what a reader may NOT conclude.

        The interesting question the archive exists for - "I changed one knob,
        did it do anything" - and the answer has to carry its own caveat. See
        archive/compare.py: attribution counts the INPUTS that changed, because
        a table showing one knob beside one moved number invites a conclusion
        that is only warranted when nothing else moved.
        """
        archive = _archive()
        if archive is None:
            raise HTTPException(503, app.state.archive_error or "no archive")
        from .archive import compare as _compare
        from .archive import harvest as _harvest_mod
        from . import evalreport as _proj

        rows = {}
        for key in (a, b):
            found = archive.surgery(key)
            if found is None:
                raise HTTPException(404, f"no archived run {key!r}")
            rows[key] = found

        # Same projection the listing does, so the diff reads the same numbers
        # the cards do. A second path to "what does this eval say" would be a
        # second opinion, on screen, in a place nobody is checking.
        for row in rows.values():
            build = str((row.get("manifest") or {}).get("build_id") or "")
            for grading in row.get("gradings") or []:
                doc = grading.get("report")
                if isinstance(doc, dict):
                    view = _proj.project_eval(doc, build)
                    view["provenance"] = grading.get("provenance") \
                        or view["provenance"]
                    grading["view"] = view
        _harvest_mod.reconcile(archive, list(rows.values()))
        return _compare.compare(rows[a], rows[b])

    @app.get("/viewer", response_class=HTMLResponse)
    def viewer() -> HTMLResponse:
        """The room itself, on the shared SerenMeninges shell.

        Was a single inline HTML string. Moved onto the family baseplate so
        Theatre's header, tabbar and token modal are the SAME ones as every
        other viewer in the constellation - a pack of five files the shell
        assembles, exactly like Observatory, Probe and Workbench.

        Side effect worth naming: the accent is now actually CONSUMED. In the
        inline version `--accent` was substituted into a CSS variable that no
        rule ever read, so the house-lights-down black was decorative in the
        payload and invisible on the page. render_from_dir wires it into the
        shell for real.
        """
        if render_from_dir is None:
            # Fail closed and SAY SO. A viewer that quietly degrades to a
            # hand-rolled page would hide a broken install behind something
            # that looks fine, and the whole point of this service is not
            # being confidently wrong.
            return HTMLResponse(
                "<h1>SerenTheatre</h1><p>The SerenMeninges UI shell is not "
                "importable, so the viewer cannot render. The API is "
                "unaffected: <a href='/api/state'>/api/state</a>.</p>"
                "<p><code>pip install seren-meninges</code></p>",
                status_code=503)
        return HTMLResponse(render_from_dir(
            _VIEWER_DIR,
            title="seren-theatre",
            brand="Seren<b>Theatre</b>",
            subtitle=f"v{APP_VERSION} · watch a model being made",
            accent=ACCENT,
        ))

    diag(f"[seren-theatre] {len(cfg.stages)} stage(s): "
         f"{', '.join(s.name for s in cfg.stages) or 'none configured'}")
    return app
