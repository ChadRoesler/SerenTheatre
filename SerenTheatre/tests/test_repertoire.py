"""Receiving a prompt book, which means opening a file somebody sent you.

Two hazards live in that sentence and they need separate guards, because
conflating them is how one of them gets missed:

  the container   a zip entry can write outside its destination - `..`,
                  absolute paths, and symlinks, which are a write primitive
                  whose NAME looks entirely harmless.
  the contents    a recipe names an `eval.script` and the harness runs it with
                  the interpreter. A recipe from somebody else is executable
                  content BY DESIGN. Fine between friends; not fine silently.

And one property that is neither, and is the whole sharing story:

  VALIDATION NEVER REFUSES. The box a bundle is given to is exactly the one
  whose ms-moe-maker might be older, absent, or configured differently.
  Refusing the gift on the machine it was given to would make the feature fail
  at precisely its purpose.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import _writerfinder as wf
from seren_theatre.app import create_app
from seren_theatre.archive import blobs as B
from seren_theatre.archive import bundle as tb
from seren_theatre.config import StageConfig, TheatreConfig

RECIPE = "name: handoff\nsize: 0.5B\n"

# THE SPLIT IS DELIBERATE AND IT MATTERS.
#
# The container and contents checks below run through `bundle.read()` directly,
# with no HTTP and no pipeline, so they run EVERYWHERE - on a plain viewer
# install, in CI, on a laptop. They are the security-critical half, and a
# security test that only runs where an optional extra happens to be installed
# is a security test that mostly does not run.
#
# The route tests need Backstage, which needs ms-moe-maker importable, so they
# skip with a reason. Skipping is honest; passing on a mounted-nothing is not.
needs_backstage = pytest.mark.skipif(
    not wf.is_importable(),
    reason=f"{wf.WRITER_DIST or 'the writer'} is not importable here, so "
           f"Backstage is not mounted and the write routes do not exist. "
           f"Install seren-theatre[stagehand] to run these.")


def written(tmp_path, data, name="b.zip"):
    """A bundle on disk, for the checks that do not need a server."""
    path = tmp_path / name
    path.write_bytes(data)
    return path


def make_zip(recipe=RECIPE, meta=None, notes="", extra=(), symlink=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(tb.RECIPE_NAME, recipe)
        if meta is not None:
            zf.writestr(tb.MANIFEST_NAME, json.dumps(meta))
        if notes:
            zf.writestr(tb.NOTES_NAME, notes)
        for name, body in extra:
            zf.writestr(name, body)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0xA000 | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    stage = tmp_path / "lab"
    stage.mkdir()
    cfg = TheatreConfig(archive={"dsn": str(tmp_path / "a.db"),
                                 "blobs": str(tmp_path / "blobs")})
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    with TestClient(create_app(cfg)) as c:
        c.cfg = cfg
        yield c


def _import(client, data, name="Book"):
    return client.post(f"/api/backstage/books?name={name}", content=data)


# ── the container ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", ["../../etc/evil", "/etc/evil",
                                   "C:/Windows/evil", "..\\..\\evil"])
def test_an_entry_that_chooses_where_it_lands_is_refused(tmp_path, entry):
    """Refusing the WHOLE archive, not skipping the entry. One of these in a
    zip does not make it a bundle with a flaw."""
    with pytest.raises(tb.UnreadableBundle):
        tb.read(written(tmp_path, make_zip(extra=[(entry, "x")])))


def test_a_symlink_entry_is_refused(tmp_path):
    """A link is a write primitive whose name looks entirely harmless."""
    with pytest.raises(tb.UnreadableBundle) as exc:
        tb.read(written(tmp_path, make_zip(symlink="data/link")))
    assert "symlink" in str(exc.value)


def test_a_zip_bomb_is_refused_on_the_header_not_after_reading_it(tmp_path):
    """The header says the unpacked size BEFORE anything is read, which is the
    only moment refusing is free."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(tb.RECIPE_NAME, RECIPE)
        zf.writestr("data/x/huge.jsonl", b"\0" * (1 << 20))
    path = tmp_path / "b.zip"
    path.write_bytes(buf.getvalue())
    # Small on disk, and honest about its unpacked size - so the guard has to
    # be reading `file_size` rather than the file length.
    got = tb.read(path)
    assert got["unpacked_bytes"] > got["bytes"] * 10


def test_a_zip_with_no_recipe_is_an_archive_not_a_prompt_book(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("something.txt", "hello")
    with pytest.raises(tb.UnreadableBundle) as exc:
        tb.read(written(tmp_path, buf.getvalue()))
    assert "prompt book" in str(exc.value)


def test_a_newer_schema_refuses_rather_than_guessing(tmp_path):
    with pytest.raises(tb.UnreadableBundle) as exc:
        tb.read(written(tmp_path, make_zip(
            meta={"schema_version": tb.SCHEMA_VERSION + 1})))
    assert "Upgrade" in str(exc.value)


def test_a_recipe_the_size_of_a_dataset_is_refused(tmp_path):
    with pytest.raises(tb.UnreadableBundle) as exc:
        tb.read(written(tmp_path, make_zip(
            recipe="x" * (tb.MAX_RECIPE_BYTES + 1))))
    assert "document" in str(exc.value)


def test_the_executable_field_scan_works_without_a_server(tmp_path):
    """The contents hazard, checked where every install will run it."""
    got = tb.read(written(tmp_path, make_zip(
        recipe="name: x\neval:\n  script: ./their_grader.py\n")))
    assert got["executes"] and "their_grader.py" in got["executes"][0]
    assert tb.read(written(tmp_path, make_zip(), "plain.zip"))["executes"] == []


# ── the contents ────────────────────────────────────────────────────────────

@needs_backstage
def test_a_recipe_that_runs_a_script_says_so_at_import_and_on_the_shelf(client):
    """REPORTED TWICE, on purpose. Once when it arrives, and again when
    somebody opens it to read - because those are two different people's
    attention and the second one is about to stage it."""
    data = make_zip(recipe="name: x\neval:\n  script: ./their_grader.py\n")
    got = _import(client, data).json()
    assert got["executes"] and "their_grader.py" in got["executes"][0]

    detail = client.get(f"/api/books/{got['book_id']}").json()
    assert detail["executes"] == got["executes"], (
        "the warning was shown at import and lost by the time somebody opened "
        "the book to read it")


@needs_backstage
def test_the_warning_is_recomputed_rather_than_stored(client):
    """So a viewer that learns about a NEW executable field starts warning
    about books imported before it knew. A flag frozen at import could only
    ever describe what that version happened to understand."""
    got = _import(client, make_zip(recipe="name: x\nsmoke:\n  script: ./s.py\n")).json()
    detail = client.get(f"/api/books/{got['book_id']}").json()
    assert any("smoke.script" in e for e in detail["executes"])


@needs_backstage
def test_an_ordinary_recipe_warns_about_nothing(client):
    assert _import(client, make_zip()).json()["executes"] == []


# ── the sharing story ───────────────────────────────────────────────────────

@needs_backstage
def test_a_bundle_that_does_not_validate_here_is_still_stored(client):
    """THE FEATURE'S WHOLE POINT. The box you were given it on is exactly the
    one that might not be able to check it."""
    got = _import(client, make_zip(recipe="name: x\nthis_is_not_valid: [\n"))
    assert got.status_code == 200, (
        "the gift was refused on the machine it was given to")
    body = got.json()
    assert body["book_id"]
    # It says what it could not check rather than pretending it did.
    assert "validated" in body


@needs_backstage
def test_the_same_bundle_twice_is_one_row(client):
    """The id is the content hash, so `we are looking at the same book` is
    checkable rather than asserted - and it is the same id your friend has."""
    data = make_zip(meta={"name": "handoff", "build_id": "cafe0001"})
    first = _import(client, data).json()["book_id"]
    second = _import(client, data).json()["book_id"]
    assert first == second
    assert len(client.get("/api/books").json()["books"]) == 1


@needs_backstage
def test_re_importing_does_not_rename_what_is_on_the_shelf(client):
    data = make_zip(meta={"name": "from-the-bundle"})
    _import(client, data, name="MyName")
    client.post("/api/backstage/books", content=data)      # no ?name=
    assert client.get("/api/books").json()["books"][0]["name"] == "MyName"


@needs_backstage
def test_a_hand_rolled_zip_with_no_manifest_still_imports(client):
    """A zip with a recipe in it is a fine thing to make by hand; refusing it
    would turn the format into a gate rather than a convenience."""
    got = _import(client, make_zip(meta=None))
    assert got.status_code == 200
    assert "makes no claim" in got.json()["meta_error"]


@needs_backstage
def test_the_notes_survive_the_round_trip(client):
    got = _import(client, make_zip(notes="# Notes\n\nepochs is the knob.\n")).json()
    detail = client.get(f"/api/books/{got['book_id']}").json()
    assert "epochs is the knob" in detail["notes"], (
        "the notes are the half a recipe cannot carry; losing them loses the "
        "part somebody wrote for you")


@needs_backstage
def test_the_bundle_is_handed_back_byte_identical(client):
    """So the person you pass it to can check it against the one you got."""
    data = make_zip(notes="hello")
    got = _import(client, data).json()
    back = client.get(f"/api/books/{got['book_id']}/bundle")
    assert back.status_code == 200 and back.content == data


# ── deletion, and the refcount that lives in the rows ───────────────────────

@needs_backstage
def test_deleting_a_book_frees_its_bytes(client, tmp_path):
    got = _import(client, make_zip()).json()
    store = B.Blobs(client.cfg.archive.blobs_dir())
    assert store.has(got["book_id"])
    assert client.delete(
        f"/api/backstage/books/{got['book_id']}").json()["bytes_freed"] is True
    assert not store.has(got["book_id"])
    assert client.get("/api/books").json()["books"] == []


@needs_backstage
def test_deleting_something_that_is_not_there_says_so(client):
    assert client.delete("/api/backstage/books/" + "0" * 64).status_code == 404


@needs_backstage
def test_a_row_whose_bytes_are_gone_still_reads_and_says_it_cannot_be_shared(
        client):
    """PRESENCE, NOT PROMISES, applied to our own blob store. A download that
    404s later is worse than a page that says the bytes are missing now."""
    got = _import(client, make_zip()).json()
    B.Blobs(client.cfg.archive.blobs_dir()).delete(got["book_id"])
    detail = client.get(f"/api/books/{got['book_id']}").json()
    assert detail["bytes_present"] is False
    assert detail["recipe"], "the recipe is stored in the row and must survive"
    assert client.get(f"/api/books/{got['book_id']}/bundle").status_code == 410


# ── the install-shape promise ───────────────────────────────────────────────

@needs_backstage
def test_reading_the_shelf_needs_no_stagehand(client):
    """Import and delete write, so they live behind [stagehand]. Looking at the
    shelf, reading a book and handing the zip on do not - the person watching
    somebody else's box should be able to do all three."""
    # getattr, not `.path`: a mounted router is in `routes` too and has no
    # path of its own. Reaching straight for the attribute worked until
    # Backstage was mounted, which is to say it worked in exactly the runs
    # where this test could not fire.
    routes = {getattr(r, "path", None) for r in client.app.routes}
    for read_route in ("/api/books", "/api/books/{book_id}",
                       "/api/books/{book_id}/bundle"):
        assert read_route in routes
    from seren_theatre.stageguard import mutating_routes
    mutating = {p for p, _ in mutating_routes(client.app)}
    assert "/api/books" not in mutating
    assert "/api/backstage/books" in mutating, (
        "importing is a write and has to be behind the extra")
