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
                     "/api/backstage/run",
                     # The repertoire. Importing and deleting a prompt book
                     # are writes and belong here; LOOKING at the shelf,
                     # reading a book and downloading the zip are read routes
                     # in app.py and stay available on a plain viewer.
                     "/api/backstage/books",
                     "/api/backstage/books/{book_id}"}, (
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
    # THE THIRD REGISTRY. It was missing from this payload while the other two
    # were here, which is backwards: the tag table is the one users are told to
    # extend, and the one whose misconfiguration is a silent wrong ANSWER
    # rather than a refusal.
    fams = {f["key"] for f in body["reasoning"]["families"]}
    assert {"deepseek", "qwen", "llama"} <= fams, body["reasoning"]
    assert {"xml", "r1"} <= {s["key"] for s in body["reasoning"]["styles"]}
    assert not body["errors"], body["errors"]


def test_a_reasoning_family_added_on_this_box_reaches_the_form(
        client, tmp_path, monkeypatch):
    """The whole argument for surfacing this registry rather than hardcoding it.

    A model family shipping a new delimiter must not have to wait for a
    release - the documented answer is to drop a yaml on the box. A form built
    from the PACKAGED table would show that person a table they have already
    moved past, and the cost of being wrong here is not a refusal: a tag style
    the splitter cannot find scores the whole think block as the answer.
    """
    user_table = tmp_path / "reasoning.yaml"
    user_table.write_text(
        "TagStyles:\n"
        "  - Key: acme_fence\n"
        "    TagStyleName: Acme Reasoning Fence\n"
        '    OpeningTag: "<<<reason"\n'
        '    ClosingTag: "reason>>>"\n'
        "    Interwoven: false\n"
        "Families:\n"
        "  - Key: acme\n"
        "    FamilyName: Acme Thinkers\n"
        "    Models: [Acme-R2, acme-thinker]\n"
        "    PreferredStyle: acme_fence\n", encoding="utf-8")
    monkeypatch.setenv("MSMOE_REASONING", str(user_table))

    body = client.get("/api/backstage").json()
    fams = {f["key"] for f in body["reasoning"]["families"]}
    assert "acme" in fams, (
        f"a family added on this box did not reach the form: {sorted(fams)}")
    # Layers merge BY NAME. Adding one family must not cost the shipped ones -
    # a form that swapped the table for the user's file would be worse than one
    # that ignored it, because it would look complete.
    assert {"deepseek", "kimi", "llama", "openthink", "qwen"} <= fams, sorted(fams)
    styles = {s["key"]: s for s in body["reasoning"]["styles"]}
    assert styles["acme_fence"]["open"] == "<<<reason"


def test_the_form_asks_the_box_rather_than_importing_it(monkeypatch):
    """The architectural property, pinned.

    Backstage forks `ms-moe-maker describe` for the same reason this module
    already forks `ms-moe-maker validate`: a second copy of a fact living in
    the viewer drifts from the one the builder uses. It did drift - the craft
    form showed a validator registry `describe` did not report, while
    `describe` reported a reasoning table the form could not see.

    If this ever goes back to `from ms_moe_maker import ...`, stubbing the fork
    stops changing the answer and this fails.
    """
    from seren_theatre import backstage as bs
    monkeypatch.setattr(bs, "_ask_the_box", lambda: {
        "kinds": [{"name": "invented", "summary": "not a real kind"}],
        "validators": [], "reasoning": {}, "templates": ["only-this"]})
    out = bs._registries()
    assert [k["name"] for k in out["kinds"]] == ["invented"]
    assert out["box"]["templates"] == ["only-this"]


def test_a_box_that_cannot_answer_shows_nothing_rather_than_a_guess(monkeypatch):
    """`describe` absent, slow, unhappy or unparseable are all one thing.

    A viewer that filled the gap from its own imports would be describing a
    package nobody is going to build with - Theatre and the console script can
    live in different venvs, which is why stagehand forks in the first place.
    """
    from seren_theatre import backstage as bs
    monkeypatch.setattr(bs, "_ask_the_box", lambda: None)
    out = bs._registries()
    assert out["kinds"] == [] and out["validators"] == []
    assert out["reasoning"] == {}
    assert any("could not ask the box" in e for e in out["errors"]), out["errors"]


def test_an_older_writer_is_named_not_silently_blank(monkeypatch):
    """`validators` and `reasoning` landed in --describe after the other keys.

    An install pinned to an earlier ms-moe-maker must say the box is too old to
    answer, not render an empty panel - "this box has no validators" and "this
    box cannot say" are different facts, and keeping them apart is the same
    discipline as keeping `unmeasurable` out of `fail`.
    """
    from seren_theatre import backstage as bs
    monkeypatch.setattr(bs, "_ask_the_box", lambda: {"kinds": ["hf", "local"]})
    out = bs._registries()
    assert out["validators"] == [] and out["reasoning"] == {}
    joined = " ".join(out["errors"])
    assert "validators" in joined and "reasoning" in joined, out["errors"]


def test_the_older_bare_name_kinds_shape_still_renders(monkeypatch):
    """Strict on what we write, lenient about what we read.

    `describe` reported `kinds` as bare strings before it reported rows, and a
    viewer meets installs on both sides of that for as long as people pin
    versions. Refusing the old shape would mean upgrading Theatre silently
    emptied the craft form of anyone who had not also upgraded the builder.
    """
    from seren_theatre import backstage as bs
    monkeypatch.setattr(bs, "_ask_the_box",
                        lambda: {"kinds": ["hf", "local", "stack"],
                                 "validators": [], "reasoning": {}})
    out = bs._registries()
    assert [k["name"] for k in out["kinds"]] == ["hf", "local", "stack"]
