"""SerenTheatre's identity card. STDLIB ONLY - nothing imported, nothing read.

WHY THIS FILE EXISTS AT ALL, since the installer already answers `--describe`
and Starwright only ever reads the installer's copy.

Because two sources that can disagree are only useful if something compares
them. `seren/port-map` records Workbench's code saying 7425 while its installer
said 7444 - an installed node answered where the docs did not, and nothing
noticed. `__main__`'s docstring has explained that reasoning for a long time;
this is the half it was explaining, and `tests/test_installer_parity.py` is the
comparison that makes the pair worth having.

STDLIB ONLY, for the same reason every other Seren service's card is: the moment
you most want something to be able to say its own name is when its install is
broken. No yaml, no meninges, no app factory.

THIS IS NOT THE INSTALLER'S COPY AND IT DOES NOT REPLACE IT. Starwright runs
`--describe` on `seren-*-setup.sh` and builds its grid from that, because the
grid has to work before anything is installed. This one is what an INSTALLED
Theatre answers, and the parity test asserts the two agree.
"""
from __future__ import annotations

NAME = "seren-theatre"
DISPLAY = "Seren Theatre"
DESCRIPTION = ("Watch a model being made. Read-only viewer over training logs "
               "and artifacts.")

# 7427, not 7426. 7426 belongs to SerenSymposium's loopback UI - the installer
# carries the same note, and the parity test is what keeps the two numbers the
# same number.
PORT = 7427

GROUP = "auxiliary"

# Every other service in the constellation gets a colour. The theatre gets the
# house lights down.
ACCENT = "#171717"

# REQUIRES NOTHING, deliberately. A stage is a directory, so Theatre can be the
# first thing installed on a box and still be useful - and the viewer half must
# stay installable on a machine with no build tooling at all.
REQUIRES: tuple = ()

# `stagehand` is the ONE extra and it is opt-in on purpose: it pulls the
# ms-moe-maker CLI, which is the half that can START a build. A viewer that
# cannot launch anything is a legitimate and common install.
EXTRAS = ("stagehand",)

DESCRIBE = {
    "schema_version": 1,
    "name": NAME,
    "display": DISPLAY,
    "description": DESCRIPTION,
    # `port`, where the installer says `default_port`. The installer is
    # describing what it WOULD use before anything exists; this is what the
    # package was built around. The parity test maps between them rather than
    # forcing one spelling on both, because they are answering slightly
    # different questions and collapsing that would hide the difference.
    "port": PORT,
    "group": GROUP,
    "accent": ACCENT,
    "package": "seren-theatre",
    "requires": list(REQUIRES),
    "extras": list(EXTRAS),
}
