"""Shared fixtures.

The one thing here is a reset, and it earns a file of its own.

`stagehand.configure()` sets MODULE state deliberately - five call sites fork
the builder and threading a config argument through all five means five chances
to forget one, where the forgotten one degrades silently to PATH. The cost of
that choice is that a test which configures a pipeline leaks it into every test
that runs after it, and the symptom would be a failure in an unrelated file
that only appears in a particular ordering. Autouse, so nobody has to remember.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_pipeline_command():
    from seren_theatre import stagehand
    stagehand.configure(None)
    yield
    stagehand.configure(None)
