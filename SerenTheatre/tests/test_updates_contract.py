"""The updates block on GET /, and the off-switch that has to actually work.

WHY THIS FILE EXISTS. Theatre shipped without an updates block entirely, and
nothing in the package noticed - it was the mirrored CI's install-shapes job
that would have caught it, on all three shapes at once, with a key-drift error
several steps removed from the cause. The family contract is that GET / always
carries a well-formed updates dict; that contract now has a test at home rather
than only in a workflow.

WHAT IS ASSERTED HERE IS THE INVARIANT, NOT THE OBSERVATION. Notably absent:
any assertion that a particular install shape reports "unavailable". That
assertion was wrong when a sibling made it and cost Chad most of a day - the
[updates] extra GUARANTEES httpx is present, but its absence guarantees
nothing, since six of nine services depend on httpx directly and
sentence-transformers drags it in anyway. Update checking simply works without
the extra. "It worked" is not a failure, and a test that says otherwise sends
someone hunting a bug that isn't there.

So: the key set is stable, the types are right, a failed check is never
reported as good news, and the off-switch is honoured.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seren_theatre.app import UPDATES_KEYS, create_app
from seren_theatre.config import TheatreConfig


@pytest.fixture
def body():
    return TestClient(create_app(TheatreConfig())).get("/").json()


# -- the block is always there and always the same shape ---------------------

def test_root_always_carries_an_updates_dict(body):
    assert isinstance(body.get("updates"), dict), (
        "GET / must always carry an updates block. Omitting it in some shapes "
        "means a consumer discovers the hole by KeyError, in the payload it "
        "most needs a straight answer from.")


def test_the_key_set_does_not_drift(body):
    assert set(body["updates"]) == set(UPDATES_KEYS)


def test_types_are_what_a_consumer_expects(body):
    u = body["updates"]
    assert isinstance(u["update_available"], bool)
    assert isinstance(u["distribution"], str) and u["distribution"]
    assert u["distribution"] == "seren-theatre"


def test_status_is_from_the_closed_vocabulary(body):
    assert body["updates"]["status"] in {"ok", "disabled", "unavailable", "error"}


def test_a_failed_check_is_never_reported_as_good_news(body):
    """The one answer this must never give.

    'unavailable' means the check could not run. If that were allowed to set
    update_available=True, or to arrive with no detail, an operator would read
    a broken checker as 'you are current' - which is precisely the state that
    leaves a friend running a service with a known problem in it.
    """
    u = body["updates"]
    if u["status"] in ("unavailable", "error"):
        assert u["update_available"] is False
        assert u["detail"], "a failed check must say how to fix it"


# -- the off-switch ----------------------------------------------------------

def test_disabling_updates_in_config_is_honoured():
    """The bug this was written for: TheatreConfig had no updates section, so
    the `updates: {enabled: false}` block the installer writes for
    --no-updates was silently dropped. It parsed clean and did nothing."""
    cfg = TheatreConfig(**{"updates": {"enabled": False}})
    assert cfg.updates.enabled is False


def test_the_installers_no_updates_block_survives_a_round_trip(tmp_path,
                                                               monkeypatch):
    """The same off-switch, down the route it actually travels.

    The test above constructs the config directly, which is a PROXY for the
    real thing - and the original bug was never about constructors. It was
    about a yaml block written by seren-theatre-setup that nothing read. So
    this writes the file the installer writes, loads it the way the service
    loads it, and asserts the switch survived the trip.

    Worth keeping both: the constructor form pins the coercion, this pins the
    path. Only one of them would have caught the original bug, and it is not
    the tidy one.
    """
    from pathlib import Path

    from seren_theatre.config import load_config

    home = tmp_path / "home"
    (home / "seren-theatre").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("SEREN_THEATRE_CONFIG", raising=False)
    monkeypatch.delenv("SEREN_THEATRE_UPDATES_ENABLED", raising=False)

    # Verbatim shape of what the installer writes for --no-updates.
    (home / "seren-theatre" / "seren-theatre.yaml").write_text(
        "server:\n  host: 127.0.0.1\n  port: 7427\n"
        "updates:\n  enabled: false\n",
        encoding="utf-8")

    cfg = load_config()
    assert cfg.updates.enabled is False, (
        "the installer's updates block was parsed and ignored - an off-switch "
        "that reports success and leaves the thing on")
    # And the block did not cost us the rest of the file.
    assert (cfg.host, cfg.port) == ("127.0.0.1", 7427)


def test_the_env_off_switch_is_honoured(tmp_path, monkeypatch):
    """`SEREN_THEATRE_UPDATES_ENABLED=false` - the family's guaranteed lever,
    and the only one a systemd unit can set without rewriting the config."""
    from pathlib import Path

    from seren_theatre.config import load_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("SEREN_THEATRE_CONFIG", raising=False)

    assert load_config().updates.enabled is True
    for falsey in ("false", "0", "no", "off", "FALSE"):
        monkeypatch.setenv("SEREN_THEATRE_UPDATES_ENABLED", falsey)
        assert load_config().updates.enabled is False, f"{falsey!r} did not disable"
    monkeypatch.setenv("SEREN_THEATRE_UPDATES_ENABLED", "true")
    assert load_config().updates.enabled is True


def test_updates_defaults_to_on():
    """On by default, off by explicit choice. Chad's call, and the security
    argument won it: a friend running a stale service with a known problem is
    worse than an outbound HTTPS call they did not specifically request."""
    assert TheatreConfig().updates.enabled is True


# -- the fallback matches the real thing -------------------------------------

def test_the_fallback_key_set_matches_meninges():
    """Pin the hand-written fallback against what seren_meninges really emits.

    The fallback only fires when meninges is not importable, which means it is
    the branch least likely to be exercised and most likely to drift. Skips
    when meninges is absent - which is honest, and is also exactly when the
    fallback is in use, so CI (where meninges IS a core dep) is where this runs.
    """
    updates = pytest.importorskip("seren_meninges.updates")
    import asyncio

    real = asyncio.run(updates.updates_payload(
        None, distribution="seren-theatre", installed="0.0.0"))
    assert set(real) == set(UPDATES_KEYS), (
        f"seren_meninges emits {sorted(set(real) ^ set(UPDATES_KEYS))} "
        f"differently from app.UPDATES_KEYS - the fallback would report a "
        f"shape the real one does not.")
