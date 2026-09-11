"""Backstage - the optional write half, and the promises it must not break.

Skipped wholesale without ms-moe-maker, which is the point: the write surface
does not exist on a base install, so there is nothing here to test on one.
"""
from __future__ import annotations

from pathlib import Path

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
                     # Eval WRITES: it forks `ms-moe-maker eval`, which
                     # generates real tokens against a built model and
                     # persists a report into the run. READING that
                     # report is a GET in app.py and stays available on a
                     # plain viewer; producing one means running the
                     # builder, so it lives behind Backstage.
                     "/api/backstage/eval",
                     # Export WRITES: it forks `bundle`, and the zip it makes
                     # goes onto the shelf. That it is here rather than beside
                     # the read routes in app.py is the whole promise - a plain
                     # viewer cannot make a bundle, only receive and pass one
                     # on, because making one means running the builder.
                     "/api/backstage/export",
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


# ── a refusal is not a server error ─────────────────────────────────────────

def test_a_build_that_refuses_is_409_and_a_build_that_crashes_is_500(tmp_path):
    """THIS PACKAGE ALREADY TOOK A POSITION ON THIS, elsewhere.

    The release workflow accepts exit 1 from a build because "a refusal is a
    legitimate answer and NOT a CI failure - it means the recipe asks for
    something the pipeline cannot honour yet, which is the tool working."

    Reporting that as 500 makes the operator's journal say ERROR and the
    browser say Internal Server Error, both of which point at Theatre. The
    thing that needs fixing is in the recipe, and it is already in the message.
    409 says "this conflicts with what this box can currently do", which is
    what actually happened.

    Exit codes OTHER than 1 stay 500 - argparse exits 2 on an unsupported flag,
    a signal is negative, and those are breakage rather than judgement.
    """
    import os
    import stat

    from fastapi.testclient import TestClient

    from seren_theatre import stagehand
    from seren_theatre.app import create_app
    from seren_theatre.config import StageConfig, TheatreConfig

    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (tmp_path / "stage").mkdir()
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "r.yaml").write_text("name: x\n", encoding="utf-8")

    cfg = TheatreConfig(archive={"dsn": str(tmp_path / "a.db")},
                        pipeline={"venv": str(tmp_path / "venv")},
                        recipes=str(recipes))
    cfg.stages = [StageConfig(name="S", path=str(tmp_path / "stage"))]

    def _fake(exit_code):
        script = venv / "ms-moe-maker"
        script.write_text(f"#!/bin/sh\necho refused >&2\nexit {exit_code}\n",
                          encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)

    for exit_code, wanted in ((1, 409), (2, 500)):
        _fake(exit_code)
        stagehand.configure(cfg.pipeline)
        with TestClient(create_app(cfg), raise_server_exceptions=False) as c:
            r = c.post("/api/backstage/run",
                       json={"name": "r.yaml", "dryrun": True})
        assert r.status_code == wanted, (
            f"a build exiting {exit_code} answered {r.status_code}, wanted "
            f"{wanted}")
        # EITHER WAY the reason travels. The whole failure this replaces was a
        # dead build reported as a live one; a status code with no explanation
        # would only move the silence somewhere else.
        assert "exited with code" in str(r.json()["detail"])


# ── a refusal arrives as data, not as a paragraph ───────────────────────────
#
# WHAT THIS IS THE FENCE FOR, and it is worth writing down because every
# assertion in this file passed while it was happening.
#
# ms-moe-maker declined to resume a run directory built under different
# settings. It named the two finished stages it would have inherited, listed
# nine changed fields, and offered three ways out by name. All of that reached
# Backstage. Backstage flattened it to str(exc) and the shell's api() - which
# throws `new Error(status + " " + statusText)` and never reads the body -
# flattened THAT to five words. The operator ssh'd into the box and tailed a
# log for a message the program had already sent him.
#
# So: the detail is a dict, the builder's own event rides inside it, and the
# prose is still there for anyone who wants only that.


def _refusing(monkeypatch, event=None, exit_code=1):
    """Make run_detached die the way a refused build dies."""
    from seren_theatre import backstage as bs

    def fake(recipe, *, cwd, log_file=None, events_file=None, extra=()):
        raise bs.stagehand.BuildDiedAtLaunch(
            exit_code, ["ms-moe-maker", "build", str(recipe), "--json"],
            "REFUSING TO RESUME: this run directory was built by a different "
            "build.", event)

    monkeypatch.setattr(bs.stagehand, "run_detached", fake)


REFUSAL_EVENT = {
    "event": "error", "stage": "build", "refusal": "resume_drift",
    "message": "run directory belongs to a different build_id",
    "headline": "REFUSING TO RESUME: this run directory was built by a "
                "different build.",
    "run_dir": "/mnt/nvme/gauntlet/msmoe_run_7B",
    "finished": ["preflight", "abliterate.base"],
    "changed": ["abliterate_n_trials: 60 -> 200"],
    "fields": [{"field": "abliterate_n_trials",
                "text": "abliterate_n_trials: 60 -> 200",
                "was": "60", "now": "200"}],
    "options": [{"id": "force", "do": "--force", "flag": "--force",
                 "what": "rebuild everything with the new settings",
                 "discards": True}],
}


@pytest.fixture
def recipe_on_disk(cfg):
    (Path(cfg.recipes_dir()) / "r.yaml").write_text(RECIPE, encoding="utf-8")
    return "r.yaml"


def test_a_refusal_carries_the_builders_own_event(
        client, cfg, recipe_on_disk, monkeypatch):
    _refusing(monkeypatch, REFUSAL_EVENT)
    resp = client.post("/api/backstage/run", json={"name": recipe_on_disk})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), (
        "the refusal was flattened to a sentence again - everything a viewer "
        "could act on is now inside a string it would have to parse")
    assert detail["event"]["refusal"] == "resume_drift"
    assert detail["event"]["finished"] == ["preflight", "abliterate.base"]
    assert detail["event"]["fields"][0]["field"] == "abliterate_n_trials"
    # AND the prose, unchanged. A caller that reads nothing else still gets
    # the whole message; the structure is an addition, not a replacement.
    assert "REFUSING TO RESUME" in detail["text"]
    assert detail["log_tail"]
    assert detail["exit_code"] == 1


def test_an_older_builder_says_nothing_structured_and_that_is_not_a_refusal(
        client, cfg, recipe_on_disk, monkeypatch):
    """None, never {}.

    An empty dict would reach the viewer as "a refusal with no changes" - a
    sentence about the run rather than about our ignorance, which is this
    repo's oldest failure. The panel keys off `event` being absent and shows
    the prose whole.
    """
    _refusing(monkeypatch, None)
    resp = client.post("/api/backstage/run", json={"name": recipe_on_disk})
    assert resp.status_code == 409
    assert resp.json()["detail"]["event"] is None
    assert "REFUSING TO RESUME" in resp.json()["detail"]["text"]


def test_breakage_is_still_a_500_and_still_carries_its_body(
        client, cfg, recipe_on_disk, monkeypatch):
    """Exit 2 is argparse rejecting a flag - breakage, not judgement. The
    status has to disagree with a refusal, and the body still has to arrive."""
    _refusing(monkeypatch, None, exit_code=2)
    resp = client.post("/api/backstage/run", json={"name": recipe_on_disk})
    assert resp.status_code == 500
    assert resp.json()["detail"]["exit_code"] == 2


def test_force_is_off_unless_asked_for(client, cfg, recipe_on_disk,
                                       monkeypatch):
    """--force DISCARDS finished stages. It must never be the default, and a
    body that does not mention it must never produce it."""
    from seren_theatre import backstage as bs
    seen = {}

    def fake(recipe, *, cwd, log_file=None, events_file=None, extra=()):
        seen["extra"] = list(extra)
        return {"pid": 7, "argv": [], "command_line": "x"}

    monkeypatch.setattr(bs.stagehand, "run_detached", fake)

    client.post("/api/backstage/run", json={"name": recipe_on_disk})
    assert "--force" not in seen["extra"], seen["extra"]

    client.post("/api/backstage/run",
                json={"name": recipe_on_disk, "force": True})
    assert "--force" in seen["extra"], seen["extra"]


# ── export: a recipe here becomes a prompt book on the shelf ────────────────
#
# THE GAP IT CLOSES. Backstage could write a recipe and start a build from it;
# the Repertoire could receive a bundle somebody else made and hand it back
# out. Nothing here could MAKE one - so the answer to "send me that gauntlet"
# was still "ssh in and run ms-moe-maker bundle", from a room that exists so
# you do not have to.
#
# These run against the REAL builder when it is here, because the whole verb is
# a fork of it and a mocked fork proves only that we built the string we meant
# to build - which is exactly the assumption that shipped `--log-file`.

import shutil
import subprocess

EXAMPLE = None
for _p in (Path(ms_moe_maker.__file__).resolve().parents[1] / "recipe.example.yaml",
           Path(ms_moe_maker.__file__).resolve().parent / "assets"
           / "recipe.example.yaml"):
    if _p.is_file():
        EXAMPLE = _p
        break

needs_bundler = pytest.mark.skipif(
    EXAMPLE is None or shutil.which("ms-moe-maker") is None,
    reason="the builder's console script or its example recipe is not here, "
           "so `bundle` cannot actually be forked - and a mocked fork would "
           "only prove we built the string we meant to build")


@pytest.fixture
def shelf(tmp_path, cfg):
    """A cfg with an archive of its own, and a real recipe on the shelf."""
    cfg.archive.dsn = str(tmp_path / "archive.db")
    cfg.archive.blobs = str(tmp_path / "blobs")
    if EXAMPLE is not None:
        shutil.copy(EXAMPLE, Path(cfg.recipes_dir()) / "gauntlet.yaml")
    return TestClient(create_app(cfg))


@needs_bundler
def test_export_makes_a_book_and_the_shelf_can_hand_it_back(shelf):
    """The whole loop, end to end: fork, stamp, store, download.

    Asserted through the READ routes on purpose. Export writing a row that
    only export can see would be a feature that works in its own test and
    nowhere else - the point of putting it on the shelf is that everything the
    shelf already does then applies to it.
    """
    r = shelf.post("/api/backstage/export",
                   json={"name": "gauntlet.yaml", "notes": "# Handoff\n\nk."})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["book_id"] and out["bytes"] > 0
    assert out["reused"] is False
    # THE EXPORTER'S OWN WORDS, kept. It prints the sixteen fields that cannot
    # travel in a recipe, with this box's values, and this is the only place a
    # person using Theatre would ever see that list.
    assert "cannot be written into a recipe" in out["output"], out["output"]

    on_shelf = shelf.get("/api/books").json()["books"]
    assert [b["book_id"] for b in on_shelf] == [out["book_id"]]
    detail = shelf.get(f"/api/books/{out['book_id']}").json()
    assert detail["bytes_present"] is True
    assert detail["notes"].startswith("# Handoff")
    assert "schema_version" in detail["recipe"]

    zipped = shelf.get(f"/api/books/{out['book_id']}/bundle")
    assert zipped.status_code == 200
    assert len(zipped.content) == out["bytes"]
    assert zipped.content[:2] == b"PK"


@needs_bundler
def test_the_stamped_recipe_leaves_nothing_for_the_far_box_to_decide(shelf):
    """The whole reason the verb exists, asserted rather than assumed.

    A recipe is mostly sentinels meaning "you decide". If export handed over
    the raw file, the far box would resolve it against ITS defaults - same
    file, different model, no error anywhere.
    """
    import yaml

    out = shelf.post("/api/backstage/export",
                     json={"name": "gauntlet.yaml"}).json()
    recipe = shelf.get(f"/api/books/{out['book_id']}").json()["recipe"]
    raw_text = (Path(shelf.app.state.cfg.recipes_dir())
                / "gauntlet.yaml").read_text(encoding="utf-8")

    # NOT len(stamped) > len(raw). The example recipe is mostly COMMENTS, which
    # the stamp does not carry, so the frozen version is the shorter file while
    # saying strictly more about the build. Comparing sizes would have been a
    # test that passed for a reason unrelated to what it is named for.
    raw, stamped = yaml.safe_load(raw_text) or {}, yaml.safe_load(recipe) or {}
    filled = [(b, k) for b, section in stamped.items()
              if isinstance(section, dict)
              for k in section
              if not isinstance(raw.get(b), dict) or k not in raw[b]]
    assert filled, (
        "not one key was filled in, so the far box is still deciding "
        "everything this box already decided")
    assert "# default" in recipe, (
        "nothing is marked as filled in from this box, so a reader cannot tell "
        "the knobs somebody CHOSE from the ones that were resolved for them")
    assert out["book_id"]
    # The header names the build this resolves to, so a mismatch elsewhere is
    # a comparison rather than a surprise.
    assert "build_id" in recipe.splitlines()[6]


@needs_bundler
def test_exporting_twice_does_not_fill_the_shelf_with_timestamp_twins(shelf):
    """`bundle` stamps the time, so an unchanged recipe exported twice is
    DIFFERENT BYTES and therefore a different content hash. Identity by hash
    is still right - it answers "did my friend get the same file" - but it is
    not the same question as "have I already got this one"."""
    first = shelf.post("/api/backstage/export",
                       json={"name": "gauntlet.yaml", "notes": "n"}).json()
    second = shelf.post("/api/backstage/export",
                        json={"name": "gauntlet.yaml", "notes": "n"}).json()
    assert second["book_id"] == first["book_id"]
    assert second["reused"] is True
    assert len(shelf.get("/api/books").json()["books"]) == 1


@needs_bundler
def test_different_notes_are_a_different_book(shelf):
    """The notes are the half a recipe cannot carry. Two bundles that say
    different things about the same build are two books."""
    a = shelf.post("/api/backstage/export",
                   json={"name": "gauntlet.yaml", "notes": "first try"}).json()
    b = shelf.post("/api/backstage/export",
                   json={"name": "gauntlet.yaml", "notes": "second try"}).json()
    assert b["book_id"] != a["book_id"]
    assert b["reused"] is False
    assert len(shelf.get("/api/books").json()["books"]) == 2


@needs_bundler
def test_export_writes_nothing_into_a_stage(shelf, tmp_path):
    """The zip is an artifact of the VIEWER. A viewer that writes into a
    watched directory has started doing the work."""
    before = sorted(p.name for p in (tmp_path / "lab").iterdir())
    shelf.post("/api/backstage/export", json={"name": "gauntlet.yaml"})
    assert sorted(p.name for p in (tmp_path / "lab").iterdir()) == before


def test_the_ceiling_refuses_by_name_and_size(shelf):
    """A --with-data bundle is the one thing that can put gigabytes in a store
    designed for small documents. Refusing and saying the size is a fine
    answer; a full disk on a box nine hours into a build is not.

    Driven through IMPORT rather than export, and that is the better test
    anyway: both verbs shelve through one function - deliberately, so the
    zip-slip and symlink refusals cannot exist in one copy and not the other -
    and import is the path where the bytes arrive from somebody else.
    """
    import io
    import os
    import zipfile

    from seren_theatre.archive import bundle as tb

    shelf.app.state.cfg.archive.max_bundle_mb = 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(tb.RECIPE_NAME, "schema_version: 1\nname: big\n")
        # Incompressible, so the ceiling is measured against a real file rather
        # than against a zip bomb's advertised size.
        zf.writestr("data/x/corpus.jsonl", os.urandom(2 * 1024 * 1024).hex())
    r = shelf.post("/api/backstage/books", content=buf.getvalue())
    assert r.status_code == 413, r.text
    assert "max_bundle_mb" in r.json()["detail"]
    assert shelf.get("/api/books").json()["books"] == [], (
        "a bundle over the ceiling was refused AND stored")


def test_no_ceiling_means_no_ceiling(cfg):
    """0 is the escape hatch for somebody who knows what they are doing, and a
    ceiling of 0 bytes would be the least useful reading of it."""
    from seren_theatre.config import ArchiveConfig

    assert ArchiveConfig(max_bundle_mb=0).max_bundle_bytes() == 0
    assert ArchiveConfig(max_bundle_mb=2048).max_bundle_bytes() == 2 * 1024**3


def test_export_needs_a_recipe_that_exists(shelf):
    r = shelf.post("/api/backstage/export", json={"name": "nope.yaml"})
    assert r.status_code == 404


@pytest.mark.parametrize("name", ["../lab/evil", "/etc/passwd", "a/b", ""])
def test_export_cannot_name_a_path(shelf, name):
    r = shelf.post("/api/backstage/export", json={"name": name})
    assert r.status_code in (400, 404, 422)


def test_notes_are_a_covering_letter_not_a_chapter(shelf):
    r = shelf.post("/api/backstage/export",
                   json={"name": "gauntlet.yaml", "notes": "x" * (300 * 1024)})
    assert r.status_code == 413


def test_export_says_so_when_there_is_no_archive(cfg, tmp_path):
    """No shelf, no export - and the reason, not a 500. The whole verb is
    'put it somewhere it survives a closed tab'."""
    cfg.archive.enabled = False
    (Path(cfg.recipes_dir()) / "r.yaml").write_text(RECIPE, encoding="utf-8")
    client = TestClient(create_app(cfg))
    r = client.post("/api/backstage/export", json={"name": "r.yaml"})
    assert r.status_code == 503
    assert "prompt book" in r.json()["detail"]


@needs_bundler
def test_a_failed_bundle_carries_its_body_and_is_not_a_500(shelf, monkeypatch):
    """Exit 2 is the STAMPER refusing its own output - it loaded the bundle
    back, resolved it, and found a field that did not survive the round trip.
    That is the tool working, and it names the fields. A 500 would point at
    Theatre and send somebody hunting their recipe for a bug in a knob table.
    """
    import seren_theatre.backstage as bs

    real = subprocess.run

    def fake(argv, **kw):
        if "bundle" in argv:
            return subprocess.CompletedProcess(
                argv, 2, "",
                "REFUSING TO WRITE: the stamped recipe does not rebuild to "
                "the same fingerprint.\n    lora_r: 16 -> 8\n")
        return real(argv, **kw)

    monkeypatch.setattr(bs.subprocess, "run", fake)
    r = shelf.post("/api/backstage/export", json={"name": "gauntlet.yaml"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["stamper_drift"] is True
    assert "lora_r" in detail["text"], (
        "the fields that moved were dropped - which is the only actionable "
        "thing in the whole message")
    assert detail["exit_code"] == 2


@needs_bundler
def test_a_bundler_that_says_it_worked_and_writes_nothing_is_reported(
        shelf, monkeypatch):
    """The difference between 'the fork failed' and 'the fork lied' is the
    first half hour of debugging."""
    import seren_theatre.backstage as bs
    real = subprocess.run

    def fake(argv, **kw):
        if "bundle" in argv:
            return subprocess.CompletedProcess(argv, 0, "  bundle -> ok\n", "")
        return real(argv, **kw)

    monkeypatch.setattr(bs.subprocess, "run", fake)
    r = shelf.post("/api/backstage/export", json={"name": "gauntlet.yaml"})
    assert r.status_code == 500
    assert "wrote no file" in r.json()["detail"]["text"]
