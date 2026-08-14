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

from fastapi import FastAPI
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
ACCENT = "#0a0a0a"

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
        return {**SERVICE, "version": APP_VERSION,
                "stages": [s.name for s in cfg.stages],
                "stagehand": stagehand,
                "updates": await _updates_block(app),
                "viewer": "/viewer", "state": "/api/state"}

    @app.get("/api/state")
    def state() -> dict:
        t0 = time.time()
        stages = [scan_stage(s.name, s.resolved(), s.logs, s.rungs,
                             cfg.tail_bytes)
                  for s in cfg.stages]
        return {"generated": t0, "took_ms": round((time.time() - t0) * 1000, 1),
                "refresh_seconds": cfg.refresh_seconds, "stages": stages,
                "version": APP_VERSION}

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
