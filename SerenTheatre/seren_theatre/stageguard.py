"""The one thing Theatre must never do, expressed as a callable.

THE INVARIANT, STATED PRECISELY.

For a long time this was enforced by a test asserting the app exposed no
POST/PUT/PATCH/DELETE anywhere. That test was a PROXY, and a good one while
Theatre was only ever a viewer. The promise it stood for was never "Theatre
has no verbs" - it was:

    THEATRE CANNOT PERTURB THE THING IT IS WATCHING.

Backstage makes the proxy wrong. Crafting and saving a recipe is a write, and
a recipe is not a stage - so the old test would forbid something that breaks
no promise, and a test that forbids safe things gets relaxed, and a relaxed
test is how the unsafe thing gets in behind it.

So the guard moves to where the meaning is: a write may happen, and it may
never land inside a directory Theatre is watching. That is narrower than the
old rule and considerably harder to satisfy by accident, because it survives
the addition of routes rather than being negotiated away by them.

WHY A FUNCTION AND NOT A CONVENTION. A convention is a sentence in a
docstring that the fourth handler forgets. This raises. Every write path calls
it, and the test suite asserts the ways around it are closed - `..`, a
symlink, a stage nested inside another, a path that does not exist yet.

READ THE `resolve()` NOTE BEFORE CHANGING ANYTHING. Both sides are resolved,
symlinks and all, precisely because a symlink is the interesting attack and
the interesting accident: `~/seren-theatre/recipes/current -> /lab/dryrun_0.5B`
is a perfectly ordinary thing for a tired person to create, and a containment
check on the un-resolved path would wave it straight through.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional


class WritesIntoStage(Exception):
    """A write was aimed at a directory Theatre is watching.

    Deliberately not an HTTPException. This module knows about the invariant,
    not about the web - a CLI, a test or a future non-HTTP caller gets the same
    refusal, and the route layer decides what status code that deserves.
    """

    def __init__(self, path: Path, stage_name: str, stage_path: Path) -> None:
        self.path = path
        self.stage_name = stage_name
        self.stage_path = stage_path
        super().__init__(
            f"refusing to write to {path}: it is inside the watched stage "
            f"{stage_name!r} ({stage_path}). Theatre observes stages and never "
            f"touches them - that is what makes it safe to point at a live run. "
            f"Write somewhere outside every configured stage (recipes have "
            f"their own directory for exactly this reason).")


def _resolved_stages(cfg) -> List[tuple]:
    """(name, resolved path) for every configured stage.

    Resolution can fail - a stage on an unmounted drive, a broken symlink - and
    a stage we cannot resolve is one we cannot prove we are OUTSIDE of. Those
    are kept, resolved as far as the OS will go, and still compared. Dropping
    them would silently shrink the protected set, which is the wrong direction
    for a safety check to fail in.
    """
    out: List[tuple] = []
    for stage in getattr(cfg, "stages", None) or []:
        raw = Path(os.path.expanduser(str(getattr(stage, "path", stage))))
        try:
            resolved = raw.resolve()
        except OSError:
            resolved = Path(os.path.abspath(str(raw)))
        out.append((getattr(stage, "name", str(raw)), resolved))
    return out


def _resolve_for_compare(path: Path) -> Path:
    """Resolve a path that may not exist yet.

    `Path.resolve()` on a non-existent file is fine on 3.6+ and resolves the
    parts that DO exist, which is exactly what is needed: the file being
    created does not exist, but the directory it is being created in does, and
    that directory is what decides containment. `strict=False` is the default
    and is being relied on deliberately - do not add strict=True.
    """
    expanded = Path(os.path.expanduser(str(path)))
    try:
        return expanded.resolve()
    except OSError:
        return Path(os.path.abspath(str(expanded)))


def is_inside_a_stage(path: Path, cfg) -> Optional[tuple]:
    """The (name, path) of the stage containing `path`, or None.

    A read-only question, safe to ask from anywhere - the viewer uses it to
    decide whether to offer an editor at all, so the refusal is not the first
    time a person hears about it.
    """
    target = _resolve_for_compare(path)
    for name, stage in _resolved_stages(cfg):
        # is_relative_to, not str.startswith. `/lab/dryrun_0.5B-old` starts
        # with `/lab/dryrun_0.5B` as a STRING and is a different directory;
        # a prefix match would refuse a legitimate write and, worse, teach
        # whoever hits it that this check is noise.
        if target == stage or target.is_relative_to(stage):
            return (name, stage)
    return None


def assert_outside_stages(path: Path, cfg) -> Path:
    """Resolve `path`, or raise WritesIntoStage. Returns the resolved path.

    RETURNING THE RESOLVED PATH IS THE POINT, not a convenience. If a caller
    checks one path and then writes to another, the check proved nothing about
    the write - so the guard hands back the exact path it approved and callers
    are expected to use that one. Any write handler should read:

        target = assert_outside_stages(requested, cfg)
        target.write_text(...)

    and never mention `requested` again.
    """
    found = is_inside_a_stage(path, cfg)
    if found is not None:
        raise WritesIntoStage(_resolve_for_compare(path), found[0], found[1])
    return _resolve_for_compare(path)


def mutating_routes(app) -> List[tuple]:
    """(path, verbs) for every route that can change something. RECURSIVELY.

    Lives here rather than in the test so the app can report its own write
    surface on GET / when Backstage is installed. A person who installed the
    optional half should be able to see exactly what it added, from the
    outside, without reading the source.

    THE RECURSION IS THE WHOLE POINT, and it was found the hard way.

    The first version walked `app.routes` one level deep. That is correct on
    older FastAPI, which flattens an included router's routes into the app.
    FastAPI 0.141 does not: `include_router` leaves a single `_IncludedRouter`
    object in `app.routes` with the real routes nested inside it.

    So the moment Backstage was mounted, this function returned [] - and
    test_the_base_install_has_no_write_verbs went on passing while five write
    routes sat one level down. A guard that reports "nothing to see" because it
    cannot see is worse than no guard, and it is the same failure as an empty
    set reading as agreement: absence of evidence rendered as evidence of
    absence.

    Hence: descend into anything carrying its own `.routes`, which covers
    included routers, Mounts and sub-applications alike, and dedupe on the way
    out because a nested router can legitimately be reachable twice.
    """
    verbs = {"POST", "PUT", "PATCH", "DELETE"}
    out: List[tuple] = []
    seen: set = set()

    def walk(container, depth: int = 0) -> None:
        # Bounded because a sub-application can hold a reference that loops.
        if depth > 8:
            return
        for route in getattr(container, "routes", None) or []:
            if id(route) in seen:
                continue
            seen.add(id(route))
            methods = getattr(route, "methods", None)
            if methods and set(methods) & verbs:
                # A TUPLE of verbs, not a list: these go through set() below to
                # dedupe a router reachable by two paths, and a list is
                # unhashable. Callers that want a list can make one.
                out.append((getattr(route, "path", "?"),
                            tuple(sorted(set(methods) & verbs))))
            # Descend into whatever this thing wraps. The attribute name is
            # version-dependent and undocumented - FastAPI 0.141's
            # `_IncludedRouter` exposes `original_router` and has no `.routes`
            # at all, where a Mount has `.app` and older versions flattened
            # everything - so several names are tried rather than one being
            # trusted. If a future version renames it again, the LAST line of
            # defence is tests/test_app.py, which asserts against the known
            # write paths rather than against this function's opinion.
            for attr in ("routes", "original_router", "router", "app"):
                inner = getattr(route, attr, None)
                if inner is None or inner is route:
                    continue
                if attr == "routes":
                    walk(route, depth + 1)
                elif getattr(inner, "routes", None) is not None:
                    walk(inner, depth + 1)

    walk(app)
    return sorted(set(out))
