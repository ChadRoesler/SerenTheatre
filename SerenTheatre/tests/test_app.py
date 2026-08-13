"""Routes, and the two promises the app makes that are easy to break quietly.

The first is READ-ONLY. Not "we don't currently write" - there is no write path
and no config knob to add one, because that's what makes it safe to point at a
live 14B run that's nine hours in. A route table is the honest place to enforce
that: if a POST/PUT/PATCH/DELETE ever appears, this fails.

The second is that DEMO MODE CANNOT BE MISTAKEN FOR A READING. _demo.py names
its own three mechanisms - explicit flag only, everything labelled in the data
itself, and demo=true in the payload. All three are tested here, including the
one that matters most: an EMPTY stage list must stay empty. A dashboard that
quietly shows fabricated data because it found nothing real is worse than a
blank page, and it is exactly the failure a tired person at 2am would not catch.
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


@pytest.fixture
def demo_client(empty_cfg) -> TestClient:
    return TestClient(create_app(empty_cfg, demo=True))


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
    assert body["stages"] == []
    assert body["demo"] is False, (
        "an empty room reported anything other than demo=false; demo must "
        "never be a fallback for having found nothing"
    )


def test_real_mode_always_states_demo_false(client):
    # Explicitly present, not merely absent. A scripted consumer asking
    # state["demo"] must get a straight answer from the payload it most needs
    # one from.
    assert "demo" in client.get("/api/state").json()


def test_a_missing_stage_directory_does_not_crash_the_room(tmp_path):
    cfg = TheatreConfig()
    cfg.stages = [StageConfig(name="Gone", path=str(tmp_path / "not-here"))]
    r = TestClient(create_app(cfg)).get("/api/state")
    assert r.status_code == 200, "a deleted stage directory took the viewer down"


# -- demo mode is loud --------------------------------------------------------

def test_demo_mode_marks_the_payload(demo_client):
    assert demo_client.get("/api/state").json()["demo"] is True


def test_every_fabricated_name_carries_its_own_label(demo_client):
    """Mechanism 2 from _demo.py: the fiction has to survive a screenshot that
    crops the banner off."""
    body = demo_client.get("/api/state").json()
    assert body["stages"], "demo mode served nothing to look at"
    for stage in body["stages"]:
        assert "DEMO" in stage["name"].upper(), (
            f"fabricated stage {stage['name']!r} is not labelled; cropped out "
            f"of the banner it would read as a real run"
        )


def test_demo_requires_the_explicit_flag(empty_cfg):
    """Mechanism 1: never a config key, never inferred."""
    assert TestClient(create_app(empty_cfg)).get("/api/state").json()["demo"] is False
    assert not hasattr(empty_cfg, "demo"), (
        "demo became a config field; it must stay an explicit runtime flag so "
        "it cannot be switched on by a file someone forgot they edited"
    )
