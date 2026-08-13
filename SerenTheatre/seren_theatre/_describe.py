"""The service's own identity card. STDLIB ONLY - nothing imported, nothing read.

Why this is its own module rather than living in config.py: `--describe` is a
contract that must work on a HALF-INSTALLED service. Starwright builds its grid
by asking every installer what it is, and the answer has to arrive even when the
runtime deps are missing, the config is malformed, or the venv is wrong. Reaching
this through config.py meant `--describe` needed pydantic, so a service whose
install had failed became a service that could not say its own name - which is
exactly the moment you most want it to.

Single source of truth, deliberately: config.py imports DEFAULT_PORT and ACCENT
from HERE, so the number the service binds to and the number it advertises
cannot drift apart. That failure is not theoretical - the `seren/port-map` fact
in Loci records Workbench's code saying 7425 while its installer said 7444, so
an installed node answered where the docs did not. One constant, two readers.

A shell installer for this service exists in the SerenStarwright repo and has
to agree with these values. Nothing HERE checks that, deliberately: Theatre
does not depend on Starwright and does not look for it. If you change a value
in this file, the installer is the other place to change.
"""
from __future__ import annotations

# seren-base36 family. 6361 lodestar · 7420 memory · 7421 margin · 7422 loci ·
# 7423 scc · 7425 workbench · 7426 SYMPOSIUM · 7430 probe · 7777 observatory.
# 7424 is NOT free - held for SerenCorpusCallosum's vector variant in Probe
# topologies. Probe's own compose topologies run 7440-7461.
#
# THIS WAS 7426 AND 7426 WAS WRONG. The reasoning was sound and the source was
# bad: 7426 is the next free seat in the `seren/port-map` fact, but that fact
# lists only the eight NETWORK services, and SerenSymposium binds 7426 on
# loopback for its UI shim (seren_symposium/config.py, DEFAULT_UI_PORT). Being
# localhost-only is exactly why it is missing from the map and exactly why it
# collides - Theatre is also a 127.0.0.1 service, so the two land on the same
# interface of the same box by construction.
#
# Note what kind of failure that is. The docstring above warns about DRIFT: one
# service, two readers, numbers pulled apart over time. This was the opposite -
# two services, two constants, neither of which ever moved. They were both
# correct and identical, and identical is the bug. A single-source-of-truth
# check inside one package cannot see it; only a map that lists every binder
# can, which is why Symposium has been added to `seren/port-map`.
#
# Symposium keeps 7426 because Symposium is the one already installed and
# running. The unshipped service moves. 7427 is free and still sits beside
# Workbench, which is all 7426 was ever chosen for.
DEFAULT_PORT = 7427

# Every other service in the constellation gets a colour. The theatre gets the
# house lights down.
ACCENT = "#000000"

NAME = "seren-theatre"
GROUP = "auxiliary"
DESCRIPTION = ("Watch a model being made. Read-only viewer over training logs "
               "and artifacts.")

DESCRIBE = {
    "name": NAME,
    "port": DEFAULT_PORT,
    "group": GROUP,
    "accent": ACCENT,
    # No optional deps. Theatre reads files off disk; there is no [mcp] surface
    # and deliberately no write path to expose one over.
    "extras": [],
    # Requires NOTHING. A stage is a directory, so Theatre can be the first
    # thing installed on a box and still be useful - which is the point of it
    # being the room rather than a plugin to something else.
    "requires": [],
    "description": DESCRIPTION,
}
