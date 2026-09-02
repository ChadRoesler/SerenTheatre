"""Routes, and the two promises the app makes that are easy to break quietly.

The first is READ-ONLY, and it is now stated in two pieces rather than one,
because the single blanket version was about to become wrong.

    * The BASE install has no write verbs at all. Backstage ships behind the
      [stagehand] extra and mounts its own router, so installing the viewer
      gets you a viewer - a claim worth being able to make to a stranger.
    * No write, in ANY install shape, may land inside a watched stage. That is
      the promise the route-table check was ever standing in for, and it is
      the one that makes Theatre safe to point at a live 14B run nine hours in.

The old rule - "no POST/PUT/PATCH/DELETE anywhere" - would have forbidden
saving a recipe, which breaks no promise, and a test that forbids safe things
gets relaxed. Relaxed is how the unsafe thing gets in behind it. See
seren_theatre/stageguard.py.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import seren_theatre
from seren_theatre.app import create_app
from seren_theatre.config import StageConfig, TheatreConfig
from seren_theatre.stageguard import (WritesIntoStage, assert_outside_stages,
                                      is_inside_a_stage, mutating_routes)


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

def test_the_base_install_has_no_write_verbs(client):
    """A plain `pip install seren-theatre` is a viewer and nothing else.

    Skipped when ms-moe-maker is importable, because then this is not a base
    install and the assertion would be false for the right reason. The other
    shape is asserted in tests/test_backstage.py, which skips in the opposite
    case - between them every install shape is covered exactly once.

    This is the OLD blanket rule, kept - but scoped to the install shape it is
    actually true of. Backstage ships in [stagehand] and mounts its own router;
    without that extra there is no write surface at all, and that is a promise
    worth being able to make to somebody deciding what to install.
    """
    if importlib.util.find_spec("ms_moe_maker") is not None:
        pytest.skip("ms-moe-maker is installed, so this is a [stagehand] "
                    "install and Backstage is SUPPOSED to have write verbs - "
                    "see tests/test_backstage.py")
    offenders = mutating_routes(client.app)
    assert offenders == [], (
        f"the BASE install grew a write path: {offenders}. Backstage and "
        f"anything else that writes belongs behind the [stagehand] extra, so "
        f"that installing the viewer gets you a viewer.")


# -- read-only where it counts: no write may land in a watched stage ----------
#
# The rule above is about install shapes. THIS is the actual promise, and it
# holds no matter what gets installed: Theatre observes stages and never
# touches them. See seren_theatre/stageguard.py for why the blanket
# no-verbs rule was the wrong place to enforce it once Backstage existed.


@pytest.fixture
def guarded(tmp_path) -> TheatreConfig:
    cfg = TheatreConfig()
    (tmp_path / "lab").mkdir()
    (tmp_path / "recipes").mkdir()
    cfg.stages = [StageConfig(name="Lab", path=str(tmp_path / "lab"))]
    return cfg


def test_a_write_outside_every_stage_is_allowed(guarded, tmp_path):
    target = assert_outside_stages(tmp_path / "recipes" / "new.yaml", guarded)
    assert target == (tmp_path / "recipes" / "new.yaml").resolve()


def test_a_write_into_a_stage_is_refused(guarded, tmp_path):
    with pytest.raises(WritesIntoStage):
        assert_outside_stages(tmp_path / "lab" / "recipe.yaml", guarded)


def test_the_stage_directory_itself_is_refused(guarded, tmp_path):
    with pytest.raises(WritesIntoStage):
        assert_outside_stages(tmp_path / "lab", guarded)


def test_dot_dot_cannot_walk_back_in(guarded, tmp_path):
    """The oldest trick, and the one a naive prefix check misses."""
    sneaky = tmp_path / "recipes" / ".." / "lab" / "run" / "recipe.yaml"
    with pytest.raises(WritesIntoStage):
        assert_outside_stages(sneaky, guarded)


def test_a_symlink_pointing_into_a_stage_is_refused(guarded, tmp_path):
    """The interesting one, and it is an ACCIDENT more often than an attack.

    `recipes/current -> /lab/dryrun_0.5B` is an ordinary thing for a tired
    person to create. Containment must be judged on the resolved path or this
    check waves the write straight through while reporting success.
    """
    link = tmp_path / "recipes" / "current"
    try:
        link.symlink_to(tmp_path / "lab", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlink support on this platform/account")
    with pytest.raises(WritesIntoStage):
        assert_outside_stages(link / "recipe.yaml", guarded)


def test_a_sibling_that_merely_shares_a_prefix_is_allowed(guarded, tmp_path):
    """`lab-old` starts with `lab` as a STRING and is a different directory.

    Refusing it would be a false positive, and false positives are how a
    safety check gets a reputation for noise and then gets removed.
    """
    (tmp_path / "lab-old").mkdir()
    assert_outside_stages(tmp_path / "lab-old" / "recipe.yaml", guarded)


def test_the_guard_answers_for_a_path_that_does_not_exist_yet(guarded, tmp_path):
    """Saving a NEW recipe is the normal case: the file is absent by
    definition, and the directory it lands in is what decides containment."""
    assert is_inside_a_stage(tmp_path / "recipes" / "nope.yaml", guarded) is None
    assert is_inside_a_stage(tmp_path / "lab" / "nope.yaml", guarded) is not None


# -- the refresh, and the two things it must not throw away -------------------
#
# THE ANNOYANCE, and it is worth stating because the fix looks like caching.
# `load()` replaced #stages-body wholesale every five seconds. Collapse state
# survived that; SCROLL POSITION did not - "you could be mid review near the
# bottom then BOOP! up to the top you go" - and during a ~58-minute fine-tune
# the manifest is not rewritten once, so roughly 700 consecutive polls rebuilt
# a page that had not changed.
#
# WHY THE INTERESTING HALF IS CHECKED IN JAVASCRIPT. The trap is that several
# payload fields are computed against the server's clock and tick on every
# poll, so a naive compare never matches and the "fix" is an elaborate no-op
# that LOOKS like it works. Nothing about that failure is visible: no error, no
# log line, just the scroll jumping again. Text assertions cannot catch it, so
# the real file is loaded into a shimmed DOM and asserted against a fixture
# board - see tests/viewer_probe.js, which is also where (2) and (3) are
# checked. The RENDERED look of it is not tested and is not testable here;
# that half was eyeballed.

PROBE = Path(__file__).resolve().parent / "viewer_probe.js"


def test_the_viewer_pack_behaves_under_a_shimmed_dom():
    """The pack is what ships, so the pack is what gets exercised.

    Skipped, NAMING WHAT IT WAITS FOR, when node is not installed - a skip with
    a sentence in it is recoverable, a silent one is how a check goes blind.
    Theatre itself needs no node and never will; this is a test-time tool.
    """
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH, so scripts.js cannot be executed. "
                    "This check is waiting for it, and until then the viewer's "
                    "refresh logic is pinned only by the substring assertions "
                    "below. Theatre does not need node to run.")
    out = subprocess.run(["node", str(PROBE), str(PACK / "scripts.js")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr or out.stdout


def test_the_viewer_reads_the_same_knob_entry_keys_the_format_declares():
    """The third end of the glossary, and the only one not pinned elsewhere.

    manifest.py carries a knobs entry verbatim and never looks inside it, so
    the names of the keys INSIDE an entry are used in exactly two places: the
    writer, and scripts.js. test_manifest_contract.py pins the writer against
    the constants; this pins the viewer against the same two, so a rename
    cannot leave the room silently drawing question marks that open onto
    nothing.
    """
    from seren_theatre import manifest as mf

    js = (PACK / "scripts.js").read_text(encoding="utf-8")
    for name in (mf.KNOB_SUMMARY, mf.KNOB_DERIVED_FROM):
        assert f".{name}" in js, (
            f"manifest.py declares {name!r} as part of a knobs entry and the "
            f"viewer never reads it. Either the format moved and scripts.js "
            f"did not, or the viewer has invented a third spelling - both "
            f"render as a `?` that explains nothing.")


def test_the_room_still_knows_which_fields_come_from_the_clock():
    """The exclusion list is the whole mechanism, and forgetting to extend it
    is INVISIBLE: the signature stops matching, the page goes quietly back to
    re-rendering every poll, and nothing says so. So the names are pinned here
    against the server that produces them."""
    js = (PACK / "scripts.js").read_text(encoding="utf-8")
    assert "CLOCK_DERIVED" in js and "structuralSignature" in js
    for field in ("stalled_for", "since_activity", "manifest_quiet_for",
                  "newest_activity_for", "quiet_for", "elapsed",
                  "generated", "took_ms"):
        assert f"'{field}'" in js, (
            f"{field} is computed from the server clock and is not in the "
            f"viewer's exclusion set; every signature will differ and the "
            f"page is back to rebuilding itself every five seconds")


def test_the_playbill_is_keyed_on_the_thing_that_cannot_change():
    """(2) `resolved` is stamped once at build start, so build_id changing is
    the only event that can change this panel. True by construction."""
    js = (PACK / "scripts.js").read_text(encoding="utf-8")
    assert "KEPT_PLAYBILLS" in js and "data-playbill" in js
    assert "scrollTop" in js, (
        "the adopted panel's scroll position is not being carried across, "
        "which was the entire complaint")


def test_a_path_is_escaped_before_it_is_given_break_points():
    """(3) Order matters and the wrong one is a hole, not a cosmetic bug: a
    manifest's defaults path is not this viewer's string to trust."""
    js = (PACK / "scripts.js").read_text(encoding="utf-8")
    assert "function breakablePath" in js
    body = js.split("function breakablePath", 1)[1].split("}", 1)[0]
    assert body.index("escapeHtml") < body.index("wbr"), (
        "breakablePath inserts <wbr> before escaping, which both breaks the "
        "escaping and renders the tag as visible text")


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

