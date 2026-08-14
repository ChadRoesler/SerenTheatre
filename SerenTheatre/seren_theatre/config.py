"""Config for SerenTheatre.

PROBE-SHAPED, and that is a decision about what Theatre IS rather than a
style preference.

Theatre and Margin look alike from the outside - localhost, no MCP surface,
requires nothing - and it is tempting to read that as one convention. It is
not. Margin is a private notebook whose entire design rests on a promise not
to read it; putting a request log on that surface would print a reader's
footprints across the one thing built on nobody reading it. Its missing
middleware is the mechanism, not an omission.

Theatre is the opposite kind of thing: a TOOL, meant to be installed by people
who are not Chad so they can watch their own Ms.MoE get made. That makes it
public-service shaped - Probe's shape - so it takes the family's shared
`server:` block, the bearer-token pointers, the TLS posture, and the request
log. Same surface, same knobs, same names as Probe/Workbench/Lodestar/SCC.

Resolution (later wins):
    1. Defaults (this file)
    2. YAML  - --config -> $SEREN_THEATRE_CONFIG -> ~/seren-theatre/seren-theatre.yaml
    3. Env   - SEREN_THEATRE_*

Lenient parse, same as the rest of the family: missing file falls back to
defaults, malformed YAML logs and falls back, one bad value falls back alone.
Postel as kindness, applied to config.

ON THE HOST DEFAULT: 127.0.0.1. Theatre reads raw training logs off disk and
re-serves them over HTTP, and a training log holds absolute paths, hostnames
and occasionally a snippet of a corpus. That is not something to put on the
LAN by accident. Widen it yourself, on purpose, if you mean to. Adopting the
shared ServerConfig makes keeping that promise an ACTIVE job - see
_server_from below, which exists entirely because the shared default is
0.0.0.0.

ON READ-ONLY: Theatre never writes into a stage. It is a room with seats, not
a workbench - the whole point is that you can point it at a live run and be
certain it cannot perturb the thing it is watching. There is deliberately no
config knob to turn that off.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# The shared server/tls blocks - ONE definition for the family, same import
# Probe and the callosum use. Theatre's own three-line ServerConfig was a
# fourth copy of a thing Meninges already owned, and it silently lacked the
# bearer-token pointers, so an operator who widened the bind had no way to put
# a token in front of it.
from seren_meninges import ServerConfig, TlsConfig

from ._diag import diag

try:
    import yaml  # type: ignore[import-untyped]
    _HAS_YAML = True
except ImportError:  # pragma: no cover - pyyaml is a hard dep, but be lenient
    _HAS_YAML = False

DEFAULT_PORT = 7427
# Stated as a constant so the override below and the docstring above cannot
# drift apart, and so a test can assert against the same name the code uses.
DEFAULT_HOST = "127.0.0.1"


@dataclass
class StageConfig:
    """One watched directory. A 'stage' is a thing being made, in public."""

    name: str
    path: str
    # Globs, relative to path, for run logs. Ordered by mtime when displayed.
    logs: List[str] = field(default_factory=lambda: ["*.log"])
    # Globs for artifact roots - the per-rung output directories.
    rungs: List[str] = field(default_factory=lambda: ["dryrun_*", "*_agent_*"])

    def resolved(self) -> Path:
        return Path(os.path.expanduser(self.path)).resolve()

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional["StageConfig"]:
        """One malformed stage entry must not sink the other four.

        Returns None instead of raising, and the caller drops it with a
        diagnostic. A config listing five stages where one has a typo should
        show you four stages and tell you about the fifth - not refuse to
        start, and not silently show you nothing.
        """
        if not isinstance(d, dict):
            return None
        name, path = d.get("name"), d.get("path")
        if not path:
            return None
        default = cls(name="", path="")
        logs = d.get("logs")
        rungs = d.get("rungs")
        return cls(
            name=str(name or Path(str(path)).name or "stage"),
            path=str(path),
            logs=[str(x) for x in logs] if isinstance(logs, list) and logs
            else default.logs,
            rungs=[str(x) for x in rungs] if isinstance(rungs, list) and rungs
            else default.rungs,
        )


@dataclass
class UpdatesConfig:
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

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "UpdatesConfig":
        d = d if isinstance(d, dict) else {}
        default = cls()
        raw = d.get("check_interval_hours")
        try:
            hours = float(raw) if raw is not None else default.check_interval_hours
        except (TypeError, ValueError):
            hours = default.check_interval_hours
        return cls(
            enabled=bool(d.get("enabled", True)),
            check_interval_hours=hours if hours > 0 else default.check_interval_hours,
            index_url=str(d.get("index_url", "") or default.index_url),
            allow_prerelease=bool(d.get("allow_prerelease", False)),
        )


@dataclass
class TheatreConfig:
    """The whole service: server + tls + updates + the stages it watches."""

    # NOT `field(default_factory=ServerConfig)`. The shared ServerConfig
    # defaults to host="0.0.0.0", so the bare default would publish training
    # logs on the LAN - see _server_from for the full note.
    server: ServerConfig = field(
        default_factory=lambda: ServerConfig(host=DEFAULT_HOST, port=DEFAULT_PORT))
    tls: TlsConfig = field(default_factory=TlsConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)
    stages: List[StageConfig] = field(default_factory=list)
    # How much of the tail of a log to read. Training logs are megabytes of
    # carriage-returned progress bars; we only ever need the end, and reading
    # the whole thing on every poll would make the DASHBOARD the reason the box
    # is busy. That would be an unusually stupid way to perturb a measurement.
    tail_bytes: int = 262_144
    refresh_seconds: float = 5.0

    def __post_init__(self) -> None:
        """Coerce raw dicts into their blocks, the way pydantic used to.

        Not nostalgia for the old base class - it closes a hole the port
        opened. pydantic validated on construction, so `TheatreConfig(**data)`
        could never produce an instance whose `.updates` was a plain dict. A
        dataclass will hand you exactly that, happily, and the failure surfaces
        much later as `'dict' object has no attribute 'enabled'` from whatever
        unlucky line read it first - by which point the config looks fine, the
        service is up, and the thing that is broken is the update checker.

        So the invariant is enforced here instead: a TheatreConfig cannot hold
        an uncoerced dict in a typed slot. load_config never relies on this -
        it builds the blocks itself - which is the point. This is for every
        other caller, and for the next person who reaches for the constructor
        because that is what the old one accepted.
        """
        if isinstance(self.server, dict):
            # Through _server_from, NOT ServerConfig.from_dict directly, so the
            # loopback guard applies on this path too. A caller passing
            # {"port": 1234} must not get 0.0.0.0 handed back.
            self.server = _server_from({"server": self.server})
        if isinstance(self.tls, dict):
            self.tls = TlsConfig.from_dict(self.tls)
        if isinstance(self.updates, dict):
            self.updates = UpdatesConfig.from_dict(self.updates)
        if isinstance(self.stages, list):
            self.stages = [
                s if isinstance(s, StageConfig) else StageConfig.from_dict(s)
                for s in self.stages
            ]
            self.stages = [s for s in self.stages if s is not None]

    @property
    def host(self) -> str:
        return self.server.host

    @property
    def port(self) -> int:
        return self.server.port


def _server_from(data: Dict[str, Any]) -> ServerConfig:
    """The shared server block, with Theatre's loopback default restored.

    THIS FUNCTION IS A GUARD, and deleting it would be a security regression
    that no test failure would obviously explain.

    `ServerConfig.from_dict` spells its host fallback `d.get("host", "0.0.0.0")`
    - a hardcoded literal with no `default_host` parameter to override it, in
    contrast to `default_port`, which is parameterised precisely because ports
    are leaf-owned. Hosts are leaf-owned too, and Theatre is the first leaf
    where the family default is the wrong answer: eight services want the LAN,
    this one is showing you the inside of your own training runs.

    So a config file with no `host:` key would come back 0.0.0.0 and Theatre
    would bind every interface while its own docstring, its installer comment
    and its tests all promised loopback. Nothing would look broken. That is
    the failure mode worth twelve lines of comment.

    The right long-term fix is a `default_host` parameter on the shared
    from_dict, matching default_port. That is a Meninges change and a family
    version bump, so it is Chad's call, not something to slip in from a leaf.
    """
    raw = data.get("server")
    raw = raw if isinstance(raw, dict) else {}
    srv = ServerConfig.from_dict(raw, default_port=DEFAULT_PORT)
    # Only when the operator did not say. An explicit `host: 0.0.0.0` is a
    # deliberate act and is honoured without argument - the point is that
    # widening should be something you did, not something that happened.
    if not raw.get("host"):
        srv.host = DEFAULT_HOST
    return srv


def _candidate_paths(explicit: Optional[str]) -> List[Path]:
    out: List[Path] = []
    if explicit:
        out.append(Path(explicit))
    env = os.environ.get("SEREN_THEATRE_CONFIG")
    if env:
        out.append(Path(env))
    out.append(Path.home() / "seren-theatre" / "seren-theatre.yaml")
    return out


def _read_yaml(path: Optional[str]) -> Dict[str, Any]:
    for candidate in _candidate_paths(path):
        if not candidate.is_file():
            continue
        if not _HAS_YAML:
            diag(f"[seren-theatre] pyyaml missing; ignoring {candidate}")
            return {}
        try:
            # encoding= IS NOT OPTIONAL. Without it Python uses the LOCALE
            # codec - cp1252 on Windows - and the yaml sample opens with a
            # box-drawing banner, so the read raises UnicodeDecodeError, the
            # except below swallows it, and a Windows operator's ENTIRE config
            # is silently ignored while they stare at the values they just set.
            # Leniency is right for a MALFORMED file; it must not be what hides
            # a file we simply failed to read.
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError("top level of the config is not a mapping")
            diag(f"[seren-theatre] config: {candidate}")
            return loaded
        except Exception as exc:  # noqa: BLE001 - lenient by design
            diag(f"[seren-theatre] {candidate} is malformed ({exc}); "
                 f"falling back to defaults")
            return {}
    return {}


def _int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _apply_env_overrides(cfg: TheatreConfig) -> TheatreConfig:
    """SEREN_THEATRE_* wins last - the deploy-time escape hatch, because a
    systemd unit can set an environment variable without rewriting a file."""
    env = os.environ

    if v := env.get("SEREN_THEATRE_HOST"):
        cfg.server.host = v
    if v := env.get("SEREN_THEATRE_PORT"):
        # Guarded, unlike the family's other leaves. `cfg.server.port = int(v)`
        # is the common spelling and it turns SEREN_THEATRE_PORT=eight thousand
        # into a ValueError traceback at import - a typo in a unit file killing
        # the service rather than being ignored with a note. Keep the guard.
        parsed = _int(v, cfg.server.port)
        if parsed == cfg.server.port and str(cfg.server.port) != str(v).strip():
            diag(f"[seren-theatre] SEREN_THEATRE_PORT={v!r} is not an int; "
                 f"keeping {cfg.server.port}")
        cfg.server.port = parsed

    # The token pointers, all three, so a widened bind can be put behind a
    # bearer without editing a file. Same names as Probe's, minus the service
    # word - one habit, nine services.
    if v := env.get("SEREN_THEATRE_BEARER_TOKEN"):
        cfg.server.bearer_token = v
    if v := env.get("SEREN_THEATRE_BEARER_TOKEN_ENV"):
        cfg.server.bearer_token_env = v
    if v := env.get("SEREN_THEATRE_BEARER_TOKEN_KEYRING"):
        cfg.server.bearer_token_keyring = v
    if v := env.get("SEREN_THEATRE_TRUST_SYSTEM_STORE"):
        cfg.tls.trust_system_store = v.strip().lower() in ("1", "true", "yes", "on")

    # The guaranteed off-switch, honoured by every service in the family.
    if v := env.get("SEREN_THEATRE_UPDATES_ENABLED"):
        cfg.updates.enabled = v.strip().lower() not in ("0", "false", "no", "off")

    # A stage from the environment, so a one-off `SEREN_THEATRE_STAGE=$PWD
    # python -m seren_theatre` works with no config file at all. That is the
    # case this service will actually be started in nine times out of ten.
    if v := env.get("SEREN_THEATRE_STAGE"):
        cfg.stages.append(StageConfig(name=Path(v).name or "stage", path=v))

    return cfg


def load_config(path: Optional[str] = None) -> TheatreConfig:
    data = _read_yaml(path)
    default = TheatreConfig()

    stages: List[StageConfig] = []
    for entry in data.get("stages") or []:
        stage = StageConfig.from_dict(entry)
        if stage is None:
            diag(f"[seren-theatre] ignoring an unusable stages: entry ({entry!r})")
            continue
        stages.append(stage)

    cfg = TheatreConfig(
        server=_server_from(data),
        tls=TlsConfig.from_dict(data.get("tls")),
        updates=UpdatesConfig.from_dict(data.get("updates")),
        stages=stages,
        tail_bytes=_int(data.get("tail_bytes"), default.tail_bytes),
        refresh_seconds=_float(data.get("refresh_seconds"),
                               default.refresh_seconds),
    )
    return _apply_env_overrides(cfg)
