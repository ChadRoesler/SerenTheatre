"""SerenTheatre - the room where you watch the thing being made.

Named for the ANATOMICAL theatre: a space built with tiered seats so people can
watch a dissection. That is the whole design brief. Not a control panel, not a
console - seating. You glance at it, you learn where the run is, you look away.

Consequences of taking that literally:
  * READ-ONLY, with no config knob to change that. A theatre cannot perturb the
    thing on the table, which is what makes it safe to point at a live 14B run.
  * The stage is a DIRECTORY, not a process. Nothing has to be instrumented,
    nothing has to import a client library, nothing has to be restarted to be
    watched. Redirect a log into a folder and it is on stage.
  * It reads the tail, never the whole file. The dashboard must never be the
    reason the box is busy.

See README.md for the ethos. The short version: everything else in the
constellation gets a colour; the theatre gets the house lights down.
"""
from __future__ import annotations

# Version flows from the git tag via setuptools-scm (written to _version.py at
# build time, read here). Fallback only fires in a bare source checkout that was
# never built. Mirrors SerenMargin/SerenLoci so the family exposes __version__
# alike.
try:
    from ._version import version as __version__
except Exception:  # noqa: BLE001 - source checkout without a build
    __version__ = "0.0.0+unknown"
