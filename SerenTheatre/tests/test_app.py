"""Routes, and the two promises the app makes that are easy to break quietly.

The first is READ-ONLY. Not "we don't currently write" - there is no write path
and no config knob to add one, because that's what makes it safe to point at a
live 14B run that's nine hours in. A route table is the honest place to enforce
that: if a POST/PUT/PATCH/DELETE ever appears, this fails.

"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import re
from pathlib import Path

import seren_theatre
from seren_theatre.app import create_app
from seren_theatre.config import StageConfig, TheatreConfig


@pytest.fixture
def empty_cfg() -> TheatreConfig:
    return TheatreConfig()


@pytest.fixture
def client(empty_cfg) -> TestClient:
    return TestClient(create_app(empty_cfg))



# -- the routes exist and answer ---------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "seren-theatre"


def test_root_advertises_where_to_go_next(client):
    body = client.get("/").json()
    assert body["viewer"] == "/viewer"
    assert body["state"] == "/api/state"
    assert body["group"] == "auxiliary"
    assert "version" in body


def test_viewer_either_renders_or_says_why(client):
    """200 with the shell installed, 503 with a clear message without it.

    What must NEVER happen is a quietly degraded hand-rolled page: that hides a
    broken install behind something that looks fine, which is the one failure
    mode this service exists to not have.
    """
    r = client.get("/viewer")
    assert r.status_code in (200, 503)
    assert r.headers["content-type"].startswith("text/html")
    if r.status_code == 503:
        assert "seren-meninges" in r.text


# -- the viewer PACK ---------------------------------------------------------
# Tested as files rather than through the route, because the files are what
# ships and because the shell that assembles them is a dependency the test
# environment may not have. render_from_dir is Meninges' job and is tested
# there; that the pack is complete, self-contained and declared is ours.

PACK = Path(seren_theatre.__file__).resolve().parent / "viewer" / "ui"
PACK_FILES = ("body.html", "tabs.html", "header_aside.html", "styles.css",
              "scripts.js")


@pytest.mark.parametrize("name", PACK_FILES)
def test_every_pack_file_exists_and_is_not_empty(name):
    f = PACK / name
    assert f.is_file(), f"the shell expects {name} in viewer/ui/"
    assert f.stat().st_size > 0


def test_the_pack_is_self_contained():
    """No CDN, no build step. It has to render on a headless box over an SSH
    tunnel with nothing installed - same reason Starwright is a TUI."""
    for name in PACK_FILES:
        body = (PACK / name).read_text(encoding="utf-8")
        for offender in ('src="http', 'href="http', "cdn.", "unpkg",
                         "jsdelivr", "@import url(http"):
            assert offender not in body, f"{name} reaches out to {offender}"


def test_every_tab_has_a_panel_and_every_panel_a_tab():
    """The shell toggles .view sections by matching ids to data-tab. A tab with
    no panel is a button that does nothing; a panel with no tab is content
    nobody can reach. Both are silent."""
    tabs = set(re.findall(r'data-tab="([^"]+)"',
                          (PACK / "tabs.html").read_text(encoding="utf-8")))
    views = set(re.findall(r'<section class="view" id="([^"]+)"',
                           (PACK / "body.html").read_text(encoding="utf-8")))
    assert tabs == views, f"tabs {sorted(tabs)} vs panels {sorted(views)}"


def test_the_pack_ships_in_the_wheel():
    """Python packaging ships modules, not data. Without the package-data glob
    the .html/.css/.js are simply absent from an installed copy and /viewer
    500s - while working perfectly from the source checkout you tested in."""
    pyproject = (Path(seren_theatre.__file__).resolve().parent.parent
                 / "pyproject.toml")
    if not pyproject.is_file():
        pytest.skip("installed copy, not a source checkout")
    text = pyproject.read_text(encoding="utf-8")
    assert "[tool.setuptools.package-data]" in text
    for ext in ("*.html", "*.css", "*.js"):
        assert f"viewer/ui/{ext}" in text, f"{ext} is not declared as package data"


def test_scripts_never_bucket_an_unknown_status_into_pending():
    """The viewer must render a status it does not recognise AS unrecognised.
    Silently painting it 'pending' would be inventing a reading."""
    js = (PACK / "scripts.js").read_text(encoding="utf-8")
    assert "unknown" in js and "known_status" in js


# -- read-only, structurally --------------------------------------------------

def test_no_route_can_write(client):
    mutating = {"POST", "PUT", "PATCH", "DELETE"}
    offenders = [
        (r.path, sorted(set(r.methods) & mutating))
        for r in client.app.routes
        if getattr(r, "methods", None) and set(r.methods) & mutating
    ]
    assert offenders == [], (
        f"SerenTheatre grew a write path: {offenders}. A theatre cannot "
        f"perturb the thing on the table - that is the whole design brief, "
        f"and it is why this is safe to point at a live run."
    )


# -- empty is a true reading --------------------------------------------------

def test_no_stages_means_no_stages(client):
    body = client.get("/api/state").json()
    assert body["stages"] == [], (
        "an empty room reported"
    )


def test_a_missing_stage_directory_does_not_crash_the_room(tmp_path):
    cfg = TheatreConfig()
    cfg.stages = [StageConfig(name="Gone", path=str(tmp_path / "not-here"))]
    r = TestClient(create_app(cfg)).get("/api/state")
    assert r.status_code == 200, "a deleted stage directory took the viewer down"

