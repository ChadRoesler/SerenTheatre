"""Backstage - the optional write half, and the promises it must not break.

Skipped wholesale without ms-moe-maker, which is the point: the write surface
does not exist on a base install, so there is nothing here to test on one.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from seren_theatre.app import create_app
from seren_theatre.config import StageConfig, TheatreConfig
from seren_theatre.stageguard import mutating_routes

ms_moe_maker = pytest.importorskip(
    "ms_moe_maker", reason="[stagehand] not installed, so Backstage does not "
                           "exist - which is itself the promise")

RECIPE = """schema_version: 1
name: msmoe-dungeonmaster
size: 0.5B
base: Qwen/Qwen2.5-0.5B-Instruct
experts:
  - {name: bestiary, source: {kind: hf, repo: x/mm, text_field: text}}
  - {name: lore, source: {kind: local, path: ~/notes}}
budget: {target_steps: 150}
moe: {dense_layers: []}
"""


@pytest.fixture
def cfg(tmp_path) -> TheatreConfig:
    (tmp_path / "lab").mkdir()
    (tmp_path / "recipes").mkdir()
    c = TheatreConfig()
    c.recipes = str(tmp_path / "recipes")
    c.stages = [StageConfig(name="Lab", path=str(tmp_path / "lab"))]
    return c


@pytest.fixture
def client(cfg) -> TestClient:
    return TestClient(create_app(cfg))


# ── the route-walker regression ─────────────────────────────────────────────

def test_mutating_routes_sees_inside_an_included_router():
    """The bug that made the read-only guard blind.

    FastAPI 0.141 does NOT flatten an included router into app.routes - it
    leaves a single `_IncludedRouter` wrapper with the real routes nested
    inside. A one-level walk therefore reported ZERO write verbs while five
    POSTs were mounted, and the test asserting the base install is read-only
    passed for entirely the wrong reason.

    This is the empty-set failure again in another costume: something that
    cannot see reporting that there is nothing there.
    """
    app = FastAPI()
    nested = APIRouter(prefix="/deep")

    @nested.post("/write")
    def _write() -> dict:
        return {}

    app.include_router(nested)
    found = mutating_routes(app)
    assert found, ("mutating_routes cannot see inside an included router, so "
                   "every write-surface assertion built on it is vacuous")
    assert found[0][0].endswith("/deep/write")
    assert "POST" in found[0][1]


def test_backstage_mounts_exactly_the_expected_write_surface(client):
    paths = {p for p, _ in mutating_routes(client.app)}
    assert paths == {"/api/backstage/recipes", "/api/backstage/validate",
                     "/api/backstage/run"}, (
        "Backstage's write surface changed. That is allowed, but it is not "
        "allowed to change QUIETLY - this list is what GET / advertises.")


def test_the_service_advertises_its_own_write_surface(client):
    body = client.get("/").json()
    assert body["backstage"] is True
    assert set(body["write_routes"]) == {p for p, _ in mutating_routes(client.app)}


# ── the guard, from the outside ─────────────────────────────────────────────

@pytest.mark.parametrize("name", ["../lab/evil", "/etc/passwd",
                                  "..\\lab\\evil", "a/b", "", ".", "..",
                                  "x" * 200])
def test_a_recipe_name_cannot_express_a_path(client, name):
    """Strict allowlist, not a blocklist. The stage guard would catch a
    traversal anyway; a name that never contained a separator cannot express
    one, and two independent defences against the oldest bug in web software
    is not excessive."""
    r = client.post("/api/backstage/recipes", json={"name": name, "text": "a: 1"})
    assert r.status_code in (400, 422)


def test_nothing_a_post_can_do_lands_inside_a_stage(client, tmp_path, cfg):
    for name in ("../lab/evil", "/etc/passwd", "....//lab//evil"):
        client.post("/api/backstage/recipes", json={"name": name, "text": "a: 1"})
    assert list((tmp_path / "lab").iterdir()) == [], (
        "a write reached a watched stage - the one thing Theatre must never do")


def test_a_recipe_that_is_a_dataset_is_refused(client):
    r = client.post("/api/backstage/recipes",
                    json={"name": "big", "text": "x" * (600 * 1024)})
    assert r.status_code == 413


# ── saving, and validating with the BUILDER's validator ─────────────────────

def test_saving_writes_the_file_and_reports_validation(client, tmp_path):
    r = client.post("/api/backstage/recipes",
                    json={"name": "dnd", "text": RECIPE})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == "dnd.yaml"
    assert body["validation"]["ok"] is True
    assert (tmp_path / "recipes" / "dnd.yaml").is_file()


def test_validation_forks_the_documented_command(client):
    """Not a second validator living in the viewer. A recipe Backstage calls
    good and the builder then refuses is the worst failure available here,
    because it is discovered on a booked GPU."""
    out = client.post("/api/backstage/validate",
                      json={"name": "x", "text": RECIPE}).json()
    assert out["ok"] is True
    assert "validate" in (out["command"] or "")


def test_a_bad_recipe_is_reported_not_hidden(client):
    out = client.post("/api/backstage/validate",
                      json={"name": "x", "text": "schema_version: 1\nname: x\n"}).json()
    assert out["ok"] is False


def test_the_form_is_built_from_the_live_registries(client):
    """So a kind or validator added by a plugin appears in Backstage without
    the viewer having heard of it - the difference between extensible in
    principle and extensible in fact."""
    body = client.get("/api/backstage").json()
    assert {"hf", "local", "synth"} <= {k["name"] for k in body["kinds"]}
    assert {"contains", "syntax"} <= {v["name"] for v in body["validators"]}


# ── the detached launcher ───────────────────────────────────────────────────

def test_a_run_hands_the_child_paths_and_opens_no_files(client, tmp_path,
                                                        monkeypatch):
    """Theatre must not hold a file handle inside a stage.

    The log belongs in the stage - that is how Theatre can see it - so the
    launcher passes --log-file and the BUILDER opens it. If stagehand held that
    descriptor, Theatre would be the process writing into a stage and the
    invariant would be true only by a technicality about which module the
    handle lived in.
    """
    from seren_theatre import stagehand

    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProc()

    # Save FIRST, with the real subprocess: saving forks `ms-moe-maker
    # validate`, which uses subprocess.run - and run() uses Popen as a context
    # manager, so a fake that is not one breaks an unrelated code path. Fake
    # only what this test is about, and only once it is needed.
    client.post("/api/backstage/recipes", json={"name": "dnd", "text": RECIPE})
    monkeypatch.setattr(stagehand.subprocess, "Popen", fake_popen)
    r = client.post("/api/backstage/run", json={"name": "dnd", "dryrun": True})
    assert r.status_code == 200, r.text
    assert r.json()["pid"] == 4242

    argv = captured["argv"]
    assert "--log-file" in argv and "--events-file" in argv
    assert "--dryrun" in argv
    # The log path points INTO the stage, and that is correct - the child
    # writes it. What matters is that no handle was opened here.
    log = argv[argv.index("--log-file") + 1]
    assert str(tmp_path / "lab") in log

    kw = captured["kwargs"]
    # DEVNULL, never PIPE: a pipe nobody reads fills and blocks the child
    # forever, which looks exactly like a training run hung mid-stage.
    import subprocess as sp
    assert kw["stdout"] == sp.DEVNULL and kw["stderr"] == sp.DEVNULL
    # And genuinely detached, or the run dies when Theatre restarts.
    assert kw.get("start_new_session") or kw.get("creationflags")


def test_running_an_absent_recipe_is_a_404_not_a_launch(client):
    assert client.post("/api/backstage/run",
                       json={"name": "nope"}).status_code == 404
