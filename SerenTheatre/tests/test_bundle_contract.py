"""Pin Theatre's bundle reader against the writer that makes them.

Same bargain as the manifest, the eval sidecar and the results documents: the
bundle is a wire format ms-moe-maker owns, Theatre implements the reading half
independently so a viewer never has to pull in a training pipeline, and the
price of two implementations is drift.

THE DUPLICATION HERE IS SHARPER THAN THE OTHERS, and worth naming. The other
formats duplicate a SHAPE; this one duplicates SAFETY CHECKS - the zip-slip
refusal, the symlink refusal, the executable-field warning. Two copies of a
security check can drift into one copy of a security check, so both sides carry
their own tests for the behaviour and this file pins the constants they share.

The alternative was making a viewer install a training pipeline before it could
open a file somebody sent it, which would mean the viewer does not get
installed - so the duplication is the right price rather than a shortcut.
"""
from __future__ import annotations

import pytest

import _writerfinder as wf
from seren_theatre.archive import bundle as tb

source = wf.find("pack.py")

needs_writer = pytest.mark.skipif(
    source is None,
    reason=f"{wf.WRITER_DIST or 'the writer'} is neither installed nor "
           f"checked out nearby; the writing half of the bundle format isn't "
           f"here to compare against.")


@needs_writer
@pytest.mark.parametrize("name", ["SCHEMA_VERSION", "MANIFEST_NAME",
                                  "RECIPE_NAME", "DATA_DIR", "NOTES_NAME",
                                  "MAX_RECIPE_BYTES", "MAX_MANIFEST_BYTES"])
def test_the_format_constants_match(name):
    theirs = wf.constants(source).get(name)
    ours = getattr(tb, name)
    assert theirs == ours, (
        f"{name}: {wf.WRITER_DIST} writes {theirs!r}, seren-theatre reads "
        f"{ours!r}. A reader looking for the wrong member name reports a "
        f"perfectly good bundle as 'not a bundle'.")


@needs_writer
def test_both_sides_warn_about_the_same_executable_fields():
    """THE ONE THAT MATTERS MOST if it drifts.

    A field the writer knows executes code and the reader does not is a recipe
    that runs somebody's script with no warning anywhere - which is the exact
    failure the warning exists to prevent, arrived at by a rename.
    """
    src = source.read_text(encoding="utf-8")
    for block, key in tb.EXECUTABLE_FIELDS:
        assert f'("{block}", "{key}")' in src, (
            f"seren-theatre warns about {block}.{key} and ms-moe-maker's pack "
            f"does not list it - or has renamed it. One of the two sides is "
            f"now silent about a field that runs code.")
