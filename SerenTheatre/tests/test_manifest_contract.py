"""Pin Theatre's manifest reader against ms-moe's writer.

seren_theatre.manifest is a SECOND implementation of a format ms-moe owns, and
that is deliberate: importing ms-moe would make a viewer depend on a training
pipeline, and Theatre's `requires` is empty on purpose. Two implementations of
one wire format is the normal cost of a protocol.

The cost of two implementations is drift, and this is where it gets paid.
Exactly the same bargain as tests/test_describe_parity.py, which compares the
shell installer's --describe against the module's: two sources that can
disagree are only useful if something compares them.

Read via `ast`, never by importing ms_moe - importing it here would create the
dependency this whole arrangement exists to avoid, and the test would then pass
for the wrong reason.

Skips cleanly when no MsMoE checkout is nearby, and fails loudly if one IS
nearby but the constants have moved.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from seren_theatre import manifest as mf

MSMOE_REL = Path("MsMoE") / "MsMoE" / "ms_moe" / "manifest.py"


def _find_ms_moe_manifest() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        for candidate in (parent / MSMOE_REL,
                          parent / "MsMoE" / "ms_moe" / "manifest.py",
                          parent / "ms_moe" / "manifest.py"):
            if candidate.is_file():
                return candidate
    return None


def _literal(node: ast.AST):
    """Evaluate a literal, INCLUDING simple arithmetic between literals.

    ast.literal_eval alone is not enough and the difference is not academic:
    ms-moe spells its timeout `15 * 60`, which is a BinOp, so literal_eval
    raises and the constant reads as None - and this file then reports a
    drift that does not exist. A contract test that cries wolf gets muted,
    and a muted contract test is worse than none.

    So arithmetic between numeric literals is folded here. Still no execution,
    still no import, still nothing but the syntax tree - `15 * 60` and `900`
    are the same VALUE and this test is about values, not spellings.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        pass
    if isinstance(node, ast.BinOp):
        left, right = _literal(node.left), _literal(node.right)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            op = type(node.op)
            if op is ast.Mult:
                return left * right
            if op is ast.Add:
                return left + right
            if op is ast.Sub:
                return left - right
            if op is ast.Div:
                return left / right
    return None


def _constants(path: Path) -> dict:
    """Module-level constant assignments, without executing anything."""
    out: dict = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                value = _literal(node.value)
                if value is not None:
                    out[target.id] = value
    return out


source = _find_ms_moe_manifest()

needs_msmoe = pytest.mark.skipif(
    source is None,
    reason="no MsMoE checkout nearby; the writer half of the format isn't "
           "here to compare against.")


@pytest.fixture(scope="module")
def writer() -> dict:
    return _constants(source)


@needs_msmoe
@pytest.mark.parametrize("name", ["MANIFEST_NAME", "SCHEMA_VERSION",
                                  "STALE_AFTER_SECONDS"])
def test_format_constants_match(writer, name):
    theirs = writer.get(name)
    ours = getattr(mf, name)
    assert theirs == ours, (
        f"{name}: ms-moe writes {theirs!r}, seren-theatre reads {ours!r}. "
        f"The two ends of the format have drifted - a viewer that looks for "
        f"the wrong filename reports every instrumented run as uninstrumented, "
        f"silently.")


@needs_msmoe
@pytest.mark.parametrize("name", ["PENDING", "RUNNING", "DONE", "SKIPPED",
                                  "FAILED", "REFUSED"])
def test_status_vocabulary_matches(writer, name):
    assert writer.get(name) == getattr(mf, name), (
        f"status {name} differs between writer and reader; the viewer would "
        f"paint a state it thinks it does not recognise.")


@needs_msmoe
def test_no_status_exists_that_the_viewer_cannot_name(writer):
    theirs = set(writer.get("STATUSES") or ())
    ours = set(mf.STATUSES)
    missing = theirs - ours
    assert not missing, (
        f"ms-moe can emit {sorted(missing)} and seren-theatre does not know "
        f"those statuses. They would render as 'unknown' - which is honest, "
        f"but this is the moment to teach the viewer instead.")


def test_the_skip_cannot_become_permanent_and_silent():
    """A skip that fires forever is not a test.

    If MsMoE is checked out but this file stopped finding it - a rename, a
    reorganised tree - every assertion above would quietly stop running and
    the suite would still be green. So: if the repo is anywhere above us, the
    manifest module must be locatable.
    """
    here = Path(__file__).resolve()
    if not any((p / "MsMoE").is_dir() for p in here.parents):
        pytest.skip("no MsMoE checkout nearby; nothing to assert")
    assert source is not None, (
        "MsMoE is present but ms_moe/manifest.py was not found. The contract "
        "check is now blind - fix the path in MSMOE_REL.")
