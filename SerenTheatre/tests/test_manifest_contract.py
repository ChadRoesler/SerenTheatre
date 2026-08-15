"""Pin Theatre's manifest reader against the writer's.

seren_theatre.manifest is a SECOND implementation of a format ms-moe-maker
owns, and that is deliberate: importing the writer would make a viewer depend
on a training pipeline, and Theatre's `requires` is empty on purpose. Two
implementations of one wire format is the normal cost of a protocol.

The cost of two implementations is drift, and this is where it gets paid.
Exactly the same bargain as the installer/module `--describe` parity check:
two sources that can disagree are only useful if something compares them.

Discovery, the AST constant reader and the anti-silence guards live in
_writerfinder, shared with test_eval_contract - see that module's header for
why none of it hardcodes a name. Read via `ast`, never by importing the
writer: importing it here would create the dependency this whole arrangement
exists to avoid, and the test would then pass for the wrong reason.
"""
from __future__ import annotations

import pytest

import _writerfinder as wf
from seren_theatre import manifest as mf

MANIFEST_MODULE = "manifest.py"
source = wf.find(MANIFEST_MODULE)

needs_writer = pytest.mark.skipif(
    source is None,
    reason=f"{wf.WRITER_DIST or 'the writer'} is neither installed nor "
           f"checked out nearby; the writing half of the format isn't here to "
           f"compare against.")


@pytest.fixture(scope="module")
def writer() -> dict:
    return wf.constants(source)


# ── the contract ────────────────────────────────────────────────────────────

@needs_writer
@pytest.mark.parametrize("name", ["MANIFEST_NAME", "SCHEMA_VERSION",
                                  "STALE_AFTER_SECONDS"])
def test_format_constants_match(writer, name):
    theirs, ours = writer.get(name), getattr(mf, name)
    assert theirs == ours, (
        f"{name}: {wf.WRITER_DIST} writes {theirs!r}, seren-theatre reads "
        f"{ours!r}. The two ends of the format have drifted - a viewer that "
        f"looks for the wrong filename reports every instrumented run as "
        f"uninstrumented, silently.")


@needs_writer
@pytest.mark.parametrize("name", ["PENDING", "RUNNING", "DONE", "SKIPPED",
                                  "FAILED", "REFUSED"])
def test_status_vocabulary_matches(writer, name):
    assert writer.get(name) == getattr(mf, name), (
        f"status {name} differs between writer and reader; the viewer would "
        f"paint a state it thinks it does not recognise.")


@needs_writer
def test_no_status_exists_that_the_viewer_cannot_name(writer):
    theirs = set(writer.get("STATUSES") or ())
    ours = set(mf.STATUSES)

    # THE EMPTY SET IS NOT AGREEMENT. Without this line the comparison below
    # passes whenever STATUSES failed to parse, because nothing minus anything
    # is nothing - which is exactly how this test spent its whole life green
    # without ever comparing the two vocabularies. The writer spells it
    # `STATUSES = (PENDING, RUNNING, ...)`, a tuple of NAMES, which plain
    # literal_eval cannot read at all.
    assert theirs, (
        f"could not read STATUSES out of {source} - so the comparison below "
        f"would be an empty set against a full one, which always passes. The "
        f"writer has probably started spelling its vocabulary in a way "
        f"_writerfinder.literal cannot fold; teach it, do not leave this "
        f"vacuous.")

    missing = theirs - ours
    assert not missing, (
        f"{wf.WRITER_DIST} can emit {sorted(missing)} and seren-theatre does "
        f"not know those statuses. They would render as 'unknown' - which is "
        f"honest, but this is the moment to teach the viewer instead.")


# ── the guards on the guard ─────────────────────────────────────────────────

def test_the_writer_is_still_derivable_from_what_theatre_declares():
    """The derivation itself must work, or everything above is decoration.

    This is the assertion the old version was missing. It does not care what
    the writer is CALLED - only that Theatre still declares one under
    [stagehand] and that a module name follows from it. Rename the project as
    often as you like; this stays true. Delete the extra, or rename it, and it
    fails loudly instead of quietly skipping every assertion above.
    """
    assert wf.WRITER_DIST, (
        f"no distribution is declared under the [{wf.WRITER_EXTRA}] extra, so "
        f"this file cannot work out whose format it is checking. Either the "
        f"extra was renamed - update WRITER_EXTRA - or stagehand lost its only "
        f"dependency, which is a bigger problem than this test.")
    assert wf.WRITER_MODULE and wf.WRITER_TOKEN


def test_the_skip_cannot_become_permanent_and_silent():
    """A skip that fires forever is not a test.

    Two INDEPENDENT signals that the writer is present: it imports (metadata /
    site-packages) or its checkout is a sibling on disk. If either says yes and
    the finder still came up empty, the contract check has gone blind and this
    says so - rather than the suite staying green while nothing is compared,
    which is precisely what happened last time.
    """
    roots, importable = wf.checkout_roots(), wf.is_importable()
    if not roots and not importable:
        pytest.skip(f"{wf.WRITER_DIST} is genuinely not here; nothing to assert")
    assert source is not None, (
        f"{wf.WRITER_DIST} IS present (checkouts={[str(r) for r in roots]}, "
        f"importable={importable}) but {wf.WRITER_MODULE}/{MANIFEST_MODULE} "
        f"was not found. The contract check is now blind.")
