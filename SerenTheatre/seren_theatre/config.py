"""Config for SerenTheatre.

Follows the SerenMemory convention (Memory leads, the rest follow) so a buddy
who set up one service already knows how to set up this one:

    * network settings live under a ``server:`` block (host/port)
    * config resolves: --config -> $SEREN_THEATRE_CONFIG ->
      ~/seren-theatre/seren-theatre.yaml -> built-in defaults
    * the file is named seren-theatre.yaml

Precedence (highest wins):
    1. Env vars  (deploy-time escape hatch - systemd Environment= lines)
    2. YAML file (operator's standing config)
    3. Defaults  (sensible per-user, localhost-only)

Lenient parse, same as the rest of the family - missing file falls back to
defaults, malformed YAML logs and falls back, one bad value falls back alone.
Postel as kindness, applied to config.

ON THE HOST DEFAULT: 127.0.0.1, like Margin and unlike Memory. Theatre reads
raw training logs off disk and re-serves them over HTTP, and a training log
holds absolute paths, hostnames and occasionally a snippet of a corpus. That
is not something to put on the LAN by accident. Widen it yourself, on purpose,
if you mean to.

ON READ-ONLY: Theatre never writes into a stage. It is a room with seats, not
a workbench - the whole point is that you can point it at a live run and be
certain it cannot perturb the thing it is watching. There is deliberately no
config knob to turn that off.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from ._diag import diag

try:
    import yaml  # type: ignore[import-untyped]
    _HAS_YAML = True
except ImportError:  # pragma: no cover - pyyaml is a hard dep, but be lenient
    _HAS_YAML = False


# ONE source of truth, two readers. The port this service BINDS to and the port
# it ADVERTISES to Starwright come from the same constant, because the
# `seren/port-map` fact in Loci records what happens when they don't: Workbench
# bound 7425 while its installer said 7444, so an installed node answered where
# the docs didn't. Re-exported here so `from .config import DEFAULT_PORT` still
# reads naturally at the call sites.
from ._describe import ACCENT, DEFAULT_PORT  # noqa: F401 - re-export


class StageConfig(BaseModel):
    """One watched directory. A 'stage' is a thing being made, in public."""

    name: str
    path: str
    # Globs, relative to path, for run logs. Ordered by mtime when displayed.
    logs: List[str] = Field(default_factory=lambda: ["*.log"])
    # Globs for artifact roots - the per-rung output directories.
    rungs: List[str] = Field(default_factory=lambda: ["dryrun_*", "*_agent_*"])

    def resolved(self) -> Path:
        return Path(os.path.expanduser(self.path)).resolve()


class UpdatesConfig(BaseModel):
    """"Is there a newer seren-theatre" checking. Cosmetic, opt-outable.

    Update checking is CORE across the family now, not an extra: it shipped as
    [updates] on the theory that phoning an index should be consent-based, and
    that failed on contact because pip records what a package REQUIRES, not
    which extras you asked for. Consent was being inferred from "can I import
    httpx", which transitive dependencies decide, not the operator - so it was
    silently ON for six of nine services and OFF for the rest, by accident.

    Chad's call: "if we dont guarentee opt-in its not opt". So it is on by
    default and the OFF SWITCH IS GUARANTEED - `updates.enabled: false` here,
    or SEREN_THEATRE_UPDATES_ENABLED=false in the environment. Both are
    honoured, which is the whole point of writing them down.

    It never upgrades anything. It asks an index and reports on /.
    """
    enabled: bool = True
    check_interval_hours: float = 6.0
    index_url: str = "https://pypi.org/pypi/{distribution}/json"
    allow_prerelease: bool = False


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT


class TheatreConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    # WAS MISSING, and its absence was silent. pydantic ignores unknown keys,
    # so the `updates: {enabled: false}` block seren-theatre-setup.sh writes
    # for --no-updates parsed fine and did NOTHING. An off-switch that reports
    # success and leaves the thing on is worse than having no off-switch, and
    # it is exactly the class of bug the family's [cfg] stamp exists for.
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)
    stages: List[StageConfig] = Field(default_factory=list)
    # How much of the tail of a log to read. Training logs are megabytes of
    # carriage-returned progress bars; we only ever need the end, and reading
    # the whole thing on every poll would make the DASHBOARD the reason the box
    # is busy. That would be an unusually stupid way to perturb a measurement.
    tail_bytes: int = 262_144
    refresh_seconds: float = 5.0

    @property
    def host(self) -> str:
        return self.server.host

    @property
    def port(self) -> int:
        return self.server.port


def _candidate_paths(explicit: Optional[str]) -> List[Path]:
    out: List[Path] = []
    if explicit:
        out.append(Path(explicit))
    env = os.environ.get("SEREN_THEATRE_CONFIG")
    if env:
        out.append(Path(env))
    out.append(Path.home() / "seren-theatre" / "seren-theatre.yaml")
    return out


def load_config(path: Optional[str] = None) -> TheatreConfig:
    data: dict[str, Any] = {}
    for candidate in _candidate_paths(path):
        if not candidate.is_file():
            continue
        if not _HAS_YAML:
            diag(f"[seren-theatre] pyyaml missing; ignoring {candidate}")
            break
        try:
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError("top level of the config is not a mapping")
            data = loaded
            diag(f"[seren-theatre] config: {candidate}")
        except Exception as exc:  # noqa: BLE001 - lenient by design
            diag(f"[seren-theatre] {candidate} is malformed ({exc}); "
                 f"falling back to defaults")
        break

    try:
        cfg = TheatreConfig(**data)
    except Exception as exc:  # noqa: BLE001
        diag(f"[seren-theatre] config rejected ({exc}); using defaults")
        cfg = TheatreConfig()

    # Env override last - the deploy-time escape hatch.
    host = os.environ.get("SEREN_THEATRE_HOST")
    if host:
        cfg.server.host = host
    port = os.environ.get("SEREN_THEATRE_PORT")
    if port:
        try:
            cfg.server.port = int(port)
        except ValueError:
            diag(f"[seren-theatre] SEREN_THEATRE_PORT={port!r} is not an int; "
                 f"keeping {cfg.server.port}")

    # The guaranteed off-switch, honoured by every service in the family. A
    # deploy-time lever has to work from the environment too, because that is
    # the only thing a systemd unit can set without rewriting the config.
    updates_env = os.environ.get("SEREN_THEATRE_UPDATES_ENABLED")
    if updates_env:
        cfg.updates.enabled = updates_env.strip().lower() not in (
            "0", "false", "no", "off")

    # A stage from the environment, so a one-off `SEREN_THEATRE_STAGE=$PWD
    # python -m seren_theatre` works with no config file at all. That is the
    # case this service will actually be started in nine times out of ten.
    stage = os.environ.get("SEREN_THEATRE_STAGE")
    if stage:
        cfg.stages.append(StageConfig(name=Path(stage).name or "stage",
                                      path=stage))
    return cfg
