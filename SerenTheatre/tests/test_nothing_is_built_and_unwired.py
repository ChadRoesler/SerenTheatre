"""A perfect module nobody calls is this codebase's recurring disease.

THE CASE THAT NAMED IT. `evalrecord.py` is a complete reader for the streaming
eval sidecar. ms-moe-maker holds a complete writer for it. A contract test pins
them together and has been green since the day it was written - and not one
byte has ever travelled between them, because the writer has no caller and the
reader's `find()` has no caller either. Two immaculate implementations of a
protocol with no traffic, under a passing test, for the life of the file.

A contract test proves the two ends AGREE. A unit test proves each end WORKS.
Neither can notice that nothing ever calls either one. So this is the third
kind of test, and it asks the only question those two cannot:

    is this module reachable from anything that runs?

ms-moe-maker carries the mirror image of this guard
(tests/test_results_reach_disk.py, `the artifact writers have a caller`). The
two packages have now found the same disease five times between them, always
the same way: someone reads the code, everything looks right, and the feature
has never once executed.

THE POINT IS NOT TO FORBID UNWIRED CODE. It is to make it a DECISION. An entry
in UNWIRED is somebody saying "yes, I know, and here is when it lands" - which
is a completely different object from a hole nobody noticed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import seren_theatre

PKG = Path(seren_theatre.__file__).resolve().parent

# Modules that are deliberately not reachable yet, and why. Each entry is a
# claim somebody made on purpose; the test insists the reason is non-empty so
# "we'll wire it later" has to at least be written down.
#
# WHEN YOU WIRE ONE, DELETE ITS LINE. A stale exemption here is this file
# becoming the very thing it exists to prevent.
UNWIRED = {
    "evalrecord":
        "THE ORIGINAL CASE, and it is fitting that this guard found it on its "
        "first run. A complete reader for the streaming eval sidecar - "
        "header, records, footer, a partial-final-line tolerance, a five-word "
        "verdict vocabulary - pinned to a complete writer by "
        "test_eval_contract, green since the day both were written, and no "
        "byte has ever passed between them: `find()` has no caller here and "
        "the writer has none over there. It stays until the eval harness "
        "streams per-item results, which is when a live 7-of-30 ticker "
        "becomes possible; the finished-report path took the other road "
        "(evalreport.py) because a playbill is a settled-state panel.",
}

# Reached by the runtime rather than by an import: entry points, the __main__
# hook, and the package inits themselves.
ENTRY_POINTS = {"__main__", "__init__", "_version"}

# A RE-EXPORT IS NOT A USE, and this exclusion is the difference between a
# guard and a decoration. `archive/__init__.py` says
#
#     from . import blobs, harvest, store
#
# which makes every module in that package look imported - so the first version
# of this test passed while blobs.py had no caller at all, which is precisely
# the state it was written to detect. A barrel init makes everything reachable
# by construction; counting it would have this guard fail OPEN, quietly, which
# is the same failure mode as the contract test it exists to complement.
#
# So references are only counted from modules that DO something.
NOT_A_USE = {"__init__.py"}


def _modules() -> dict:
    """Every module in the package, as dotted names relative to it."""
    out = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(PKG).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] in ENTRY_POINTS:
            continue
        out[".".join(parts)] = path
    return out


def _imported_names(path: Path) -> set:
    """Every module name this file could be referring to.

    Deliberately GENEROUS rather than exact: `from .archive import harvest`,
    `from . import stagehand as _s`, `import seren_theatre.sources`, and a
    function-local import three frames down all count. A guard that tried to
    resolve relative imports precisely would be a small import system, and its
    bugs would fail OPEN - it would stop finding references and go quietly
    green, which is the same failure it exists to catch. Over-counting can only
    ever hide an unwired module; it can never invent one, and hiding is the
    lesser sin here because the entry is a decision either way.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    # Docstrings out: several of these modules DISCUSS the modules they do not
    # import, and a guard that matches its own prose is not a guard. That
    # mistake has already been made once in this session, in this package.
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            node.value.value = ""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.replace("seren_theatre.", ""))
                out.add(alias.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            base = (node.module or "").replace("seren_theatre.", "")
            for alias in node.names:
                out.add(alias.name)
                out.add(f"{base}.{alias.name}" if base else alias.name)
            if base:
                out.add(base)
                out.add(base.rsplit(".", 1)[-1])
    return out


def _references() -> set:
    """Every module name referred to by a module that actually does something.

    See NOT_A_USE. Package inits are excluded because a barrel re-export makes
    its whole package look wired, which would let this guard go green over the
    exact situation it exists to find.
    """
    out = set()
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts or path.name in NOT_A_USE:
            continue
        out |= _imported_names(path)
    return out


def test_every_module_is_reachable_or_declared_unwired():
    """The question a contract test and a unit test both cannot ask."""
    modules = _modules()
    references = _references()

    unreachable = []
    for name in modules:
        leaf = name.rsplit(".", 1)[-1]
        if name in references or leaf in references:
            continue
        if name in UNWIRED:
            continue
        unreachable.append(name)

    assert not unreachable, (
        f"these modules are not imported by anything in the package: "
        f"{sorted(unreachable)}.\n"
        f"That is how a complete, correct, fully tested feature runs zero "
        f"times - evalrecord.py did it for the life of the file under a green "
        f"contract test. Wire it, or add it to UNWIRED with the reason and "
        f"when it lands.")


def test_every_unwired_entry_is_still_unwired():
    """A stale exemption turns this file into the disease it guards against.

    The moment blobs.py gets an importer, this fails and the line comes out -
    which is the only mechanism that stops UNWIRED silently becoming a list of
    things that were fixed years ago.
    """
    modules = _modules()
    references = _references()

    now_wired = [name for name in UNWIRED
                 if name in references
                 or name.rsplit(".", 1)[-1] in references]
    assert not now_wired, (
        f"{now_wired} are imported now - delete their UNWIRED entries. An "
        f"exemption nobody removes is how this list stops meaning anything.")

    gone = [name for name in UNWIRED if name not in modules]
    assert not gone, f"UNWIRED names modules that no longer exist: {gone}"


@pytest.mark.parametrize("name, reason", sorted(UNWIRED.items()))
def test_an_exemption_has_to_say_when_it_lands(name, reason):
    """"We'll wire it later" has to at least be written down.

    A one-word reason is an exemption nobody can evaluate; a sentence naming
    the thing that will call it is a plan somebody can hold you to.
    """
    assert len(reason) > 60, f"{name}'s exemption does not explain itself"
    assert any(word in reason.lower()
               for word in ("wire", "lands", "until", "when")), (
        f"{name}'s exemption says why it exists but not what would end it")
