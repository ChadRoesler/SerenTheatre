"""Pin Theatre's artifact table against the pipeline that writes the artifacts.

`_STAGES` is a hardcoded list of directory names that another project chooses,
and it has already been wrong once in the worst possible way: the writer renamed
`qwen_coder_*` / `fraunkenstein_moe_untrained` / `fraunkenstein_agent_final` to
`specialist_*` / `moe_untrained` / `moe_trained`, and until that landed here
every modern run showed up with no artifacts at all. Not an error - an empty
stage. The failure was silence, which is the shape this repo keeps paying for.

ms-moe-maker now PUBLISHES the vocabulary (`--describe` carries a `stages` key
built from the same constants), so the drift is finally checkable. Theatre still
keeps its own copy rather than asking, and that is deliberate:

  * `requires` is empty on purpose. A viewer that imported a training pipeline
    to read a directory listing would be a viewer nobody can install.
  * `looks_like_run`, `scan_run` and `evaluable` run on every five-second
    poll. Backstage can afford to fork `describe` because it does so when a tab
    opens; the scanner cannot fork anything, ever.

So: two copies, and this is where the cost of two copies gets paid - the same
bargain as test_manifest_contract, read through `ast` and never by importing.

THE PATTERNS ARE DERIVED HERE, NOT RESTATED. Writing `"specialist_*"` in this
file would compare a literal against a literal and pass forever; the expected
globs are built from the writer's own `ARTIFACTS` and `FINETUNE_ARTIFACT`, so
a rename over there changes what this test demands.
"""
from __future__ import annotations

import pytest

import _writerfinder as wf
from seren_theatre import sources as src

STAGES_MODULE = "stages.py"
source = wf.find(STAGES_MODULE)

needs_writer = pytest.mark.skipif(
    source is None,
    reason=f"{wf.WRITER_DIST or 'the writer'} is neither installed nor "
           f"checked out nearby; the half that writes the artifacts isn't "
           f"here to compare against.")

#: The artifact names this viewer keeps for runs that predate the rename. They
#: are NOT part of any current contract and never will be - the writer cannot
#: publish a name it has stopped using - so they are exempted by declaration.
#: Deleting a run's history is not a migration, hence the exemption exists at
#: all; `test_no_legacy_name_is_secretly_current` is what stops it rotting.
LEGACY_PATTERNS = frozenset({
    "qwen_coder_*",
    "fraunkenstein_moe_untrained",
    "fraunkenstein_agent_final",
})


@pytest.fixture(scope="module")
def writer() -> dict:
    return wf.constants(source)


@pytest.fixture(scope="module")
def published(writer) -> dict:
    """The writer's artifact directories as Theatre's glob patterns.

    `specialist_{expert}` becomes `specialist_*` because Theatre matches
    directories rather than expanding a template - and `_expert_from` strips a
    trailing-`*` prefix to recover the expert, so the shape matters, not just
    the string.
    """
    template = writer["FINETUNE_ARTIFACT"]
    artifacts = writer["ARTIFACTS"]
    return {
        src.SPECIALISTS_STAGE: {
            template.replace(writer["EXPERT_TEMPLATE"], "*")},
        src.SKELETON_STAGE: {artifacts[writer["STITCH"]]},
        src.FINAL_STAGE: {artifacts[writer["ROUTER"]]},
    }


# ── the anti-silence guard, which is the whole reason this file is careful ───

@needs_writer
@pytest.mark.parametrize("name", ["ARTIFACTS", "FINETUNE_ARTIFACT",
                                  "FINETUNE_PREFIX", "EXPERT_TEMPLATE",
                                  "STITCH", "ROUTER", "LABELS"])
def test_the_constant_this_test_depends_on_was_actually_read(writer, name):
    """Every assertion below is worthless if the reader returned None.

    NOT hypothetical. `ARTIFACTS: Dict[str, str] = {...}` is an AnnAssign
    holding a Dict, and _writerfinder could read neither when this file was
    written - so the first version of this contract compared Theatre's table
    against nothing and passed. This is the guard for that, and it fails LOUD
    rather than skipping, because a skip is how the last one went quiet.
    """
    assert writer.get(name) is not None, (
        f"{name} is declared in {STAGES_MODULE} and _writerfinder read it as "
        f"None, so every comparison against it below is vacuous. The reader "
        f"needs to learn whatever syntax it is now written in - see literal() "
        f"and constants() in _writerfinder.")


# ── the contract ────────────────────────────────────────────────────────────

@needs_writer
def test_every_artifact_the_writer_produces_is_one_this_viewer_looks_for(
        published):
    """Completeness. This is the assertion the rename would have failed."""
    for stage, wanted in published.items():
        ours = {pattern for pattern, _marker in src._rows_for(stage)}
        missing = wanted - ours
        assert not missing, (
            f"{wf.WRITER_DIST} writes {sorted(missing)} for the {stage!r} "
            f"stage and this viewer does not look for it. Every run using the "
            f"current pipeline would render with that artifact missing - not "
            f"an error, an empty stage.")


@needs_writer
def test_no_pattern_is_looked_for_that_nobody_writes(published):
    """The other direction: Theatre must not accumulate orphan globs.

    A pattern that matches nothing any writer produces is dead weight that
    still costs a regex on every scandir of every candidate directory, and it
    reads to the next person as a name still in use.
    """
    current = {p for patterns in published.values() for p in patterns}
    for stage, pattern, _marker in src._STAGES:
        assert pattern in current or pattern in LEGACY_PATTERNS, (
            f"{pattern!r} (stage {stage!r}) is neither an artifact "
            f"{wf.WRITER_DIST} currently writes nor a declared legacy name. "
            f"If it is historical, add it to LEGACY_PATTERNS with the reason; "
            f"if it is new, the writer should be publishing it.")


@needs_writer
def test_the_two_moe_directories_are_not_swapped(writer, published):
    """WHICH stage writes WHICH directory, not just that both are known.

    `moe_untrained` and `moe_trained` differ by one word and Theatre paints
    them as different things - skeleton versus final. Swapping them would keep
    both patterns present and every completeness check green while the viewer
    reported an untrained skeleton as the finished model.
    """
    artifacts = writer["ARTIFACTS"]
    skeleton, = published[src.SKELETON_STAGE]
    final, = published[src.FINAL_STAGE]
    assert skeleton == artifacts[writer["STITCH"]]
    assert final == artifacts[writer["ROUTER"]]
    assert skeleton != final, (
        "the stitch and router stages now write the same directory, so this "
        "viewer cannot tell a skeleton from a finished MoE.")


@needs_writer
def test_the_specialist_glob_can_still_recover_an_expert_name(writer,
                                                              published):
    """The pattern is not only matched, it is arithmetic on later.

    `_expert_from` strips the prefix by length and only for a plain trailing
    `*`. A writer template like `{expert}_specialist` would still produce a
    valid glob and would silently make every expert name render as the whole
    directory.
    """
    glob, = published[src.SPECIALISTS_STAGE]
    real = writer["FINETUNE_ARTIFACT"].replace(
        writer["EXPERT_TEMPLATE"], "powershell")
    assert src._expert_from(real, glob) == "powershell", (
        f"{glob!r} no longer yields the expert from {real!r}; the viewer would "
        f"label every specialist with its directory name.")


@needs_writer
def test_no_legacy_name_is_secretly_current(published):
    """Stops the exemption list rotting into a hole.

    If the writer ever ships a directory this file has written off as
    historical, the exemption would hide a real contract from every check
    above.
    """
    current = {p for patterns in published.values() for p in patterns}
    overlap = current & LEGACY_PATTERNS
    assert not overlap, (
        f"{sorted(overlap)} is exempted here as legacy and "
        f"{wf.WRITER_DIST} writes it today. Delete it from LEGACY_PATTERNS so "
        f"the real contract is checked.")


@needs_writer
def test_the_finetune_prefix_and_artifact_agree_about_the_placeholder(writer):
    """Both templates take the same substitution, or the derivation is luck."""
    assert writer["EXPERT_TEMPLATE"] in writer["FINETUNE_ARTIFACT"], (
        "the specialist artifact template no longer contains the placeholder "
        "the writer publishes, so turning it into a glob is guesswork.")


# ── the started event, whose keys the writer now publishes ───────────────────

DESCRIBE_MODULE = "describe.py"
card = wf.find(DESCRIBE_MODULE)

needs_card = pytest.mark.skipif(
    card is None,
    reason=f"{wf.WRITER_DIST or 'the writer'}'s identity card is not here to "
           f"compare against.")


@pytest.fixture(scope="module")
def published_card() -> dict:
    return wf.constants(card)


@needs_card
def test_the_event_kind_this_viewer_looks_for_is_one_the_writer_emits(
        published_card):
    """`sources.STARTED_EVENT` against the writer's own event vocabulary.

    Theatre names the string itself rather than importing it, for the reason the
    whole contract arrangement exists - sources.py is on the viewer's import
    graph and the builder must not be. So the names get compared here instead.
    """
    emitted = published_card.get("EVENTS")
    assert emitted, (
        "EVENTS was read as empty, so this comparison is vacuous - see the "
        "anti-silence guard above for why that matters more than it looks.")
    assert src.STARTED_EVENT in emitted, (
        f"this viewer reads {src.STARTED_EVENT!r} events and "
        f"{wf.WRITER_DIST} emits {sorted(emitted)}. A viewer waiting for an "
        f"event nobody sends reports every launch as structurally silent.")


@needs_card
def test_every_field_this_viewer_reads_is_one_the_writer_publishes(
        published_card):
    """The keys, not just the envelope.

    THIS IS THE ONE THAT WOULD HAVE CAUGHT THE ORIGINAL BUG'S TWIN. Theatre
    reads `run_dir` out of that event to find the run directory; if the builder
    renamed the kwarg, the link would silently stop being made and the stage
    would fall back to newest-wins with nothing said. Silence again.
    """
    fields = published_card.get("STARTED_FIELDS")
    assert fields, (
        "STARTED_FIELDS was read as None, so nothing below is being compared. "
        "Either the writer stopped publishing it or _writerfinder cannot read "
        "the syntax it is written in.")
    for name in ("run_dir", "env_applied", "name", "size", "experts"):
        assert name in fields, (
            f"seren-theatre reads {name!r} from the started event and "
            f"{wf.WRITER_DIST} does not publish it as part of that contract.")

