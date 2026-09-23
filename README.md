# SerenTheatre

**Watch a model being made.** A viewer over training logs and artifacts — and,
with one extra installed, the room you hand a recipe to.

Named for the *anatomical* theatre — a room built with tiered seats so people can
watch a dissection. That's the whole design brief. Not a control panel, not a
console. Seating. You glance at it, you learn where the run is, you look away.

Everything else in the constellation gets an accent colour. The theatre gets the
house lights down.

---

## The two rules everything else falls out of

**1. Stagehand does the work; the theatre shows the data.**

Theatre never builds anything in its own process. The `[stagehand]` extra owns
the build and *forks a subprocess* rather than importing torch into the viewer —
a stagehand is not on stage. It invokes the same CLI a human would type, on
purpose: if the automated path and the hand-run path ever diverge, the hand-run
path is the one that rots, because it's the one with no users.

There are two install shapes, and they are different rooms:

```bash
pip install seren-theatre              # a viewer. Zero write verbs.
pip install 'seren-theatre[stagehand]' # a workshop: Backstage, and routes that run one
```

The plain install can *say* whether a stagehand exists on the box and can never
use one. The `[stagehand]` install grows a **Backstage** tab where you write a
recipe, validate it with the builder's own validator, and hand it to the
stagehand — which still forks the documented command. Nothing about the viewer
changes; the workshop is added beside it.

**2. A stage is a directory.**

Not a process, not a client library, not an agent you have to install. Nothing
has to be instrumented to be watched. Redirect a run's log into a folder and
that folder is on stage. This is why Theatre `requires` nothing and can be the
first thing installed on a box.

The run directory is also the *only* interface between the pipeline and the
viewer, which is what lets `ms-moe-maker` and `seren-theatre` be separate packages
that don't know the other exists. Install either one alone; both work.

---

## Quick start

The fastest possible version — no config file at all:

```bash
pip install seren-theatre
SEREN_THEATRE_STAGE=/mnt/nvme/fraunkensteinLab python -m seren_theatre
```

Then open <http://127.0.0.1:7427/viewer>.

That's the case you'll actually be in nine times out of ten: you've got a run
going in some directory and you want to see it. For anything longer-lived, copy
`seren-theatre.yaml.sample` to `~/seren-theatre/seren-theatre.yaml` and list your
stages there.

### Install with the installer

```bash
bash seren-theatre-setup.sh              # build from the repo checkout
bash seren-theatre-setup.sh --service    # + autostart
bash seren-theatre-setup.sh --stage /mnt/nvme/fraunkensteinLab
```

Or just let SerenStarwright find it — the installer answers `--describe`, so it
shows up in the grid with no edit to Starwright.

---

## Routes

Every install has these, and all of them are reads:

| Route                  | What it is                                              |
|------------------------|---------------------------------------------------------|
| `/viewer`              | The room itself. Auto-refreshing.                       |
| `/api/state`           | The same thing as JSON, if you'd rather script it.      |
| `/api/archive`         | Previous surgeries: every run this box has seen, kept.  |
| `/api/archive/diff`    | Two runs side by side.                                  |
| `/api/archive/sweep`   | A family of runs that differ by one knob.               |
| `/api/books`           | The repertoire: prompt books you can hand to a friend.  |
| `/health`              | Liveness.                                               |
| `/`                    | Service info, the version, and `write_routes`.          |

`write_routes` on `/` is the list of every POST/DELETE this process mounted. On
a plain install it is empty, and `test_the_base_install_has_no_write_verbs`
keeps it that way. With `[stagehand]` it lists the Backstage routes under
`/api/backstage`: save a recipe, validate one, run one, eval a finished build,
export a prompt book, and delete a book you imported. You can read the write
surface from outside without reading the source, whichever room you are in.

---

## Three deliberate constraints

**Read-only by default, and the write half is an install, not a setting.** A
theatre cannot perturb the thing on the table. That's precisely what makes it
safe to point at a live 14B run that's been going for nine hours. There is no
config option that turns writes on: the plain package mounts no write route,
and the only way to get one is to install `[stagehand]`. Even then, every write
goes through a guard that refuses to touch a watched stage — recipes live in
their own directory outside every stage, and a build's log is written by the
builder into the run, the same as when a person launches it by hand.

**Binds 127.0.0.1 by default** — like Margin, unlike Memory. A training log
carries absolute paths, hostnames and the occasional snippet of a corpus. That
isn't something to put on the LAN by accident. Widen it yourself, deliberately,
if you mean to.

**Reads the tail, never the whole file.** Training logs are megabytes of
carriage-returned progress bars and only the end is ever interesting. Tunable
via `tail_bytes`, default 256 KiB. The dashboard must never be the reason the
box is busy — that would be an unusually stupid way to perturb a measurement.

---

## Config

Resolves in this order, first hit wins:
`--config` → `$SEREN_THEATRE_CONFIG` → `~/seren-theatre/seren-theatre.yaml` →
built-in defaults.

Precedence, highest first: **env vars** (deploy-time escape hatch, for
`Environment=` lines in a unit file) → **YAML file** → **defaults**.

Parsing is lenient, same as the rest of the family. Missing file falls back to
defaults; malformed YAML logs and falls back; one bad value falls back alone.
Postel's law as a kindness — train strict, infer lenient.

| Env var                  | Overrides                          |
|--------------------------|------------------------------------|
| `SEREN_THEATRE_CONFIG`   | Config file path                   |
| `SEREN_THEATRE_HOST`     | `server.host`                      |
| `SEREN_THEATRE_PORT`     | `server.port`                      |
| `SEREN_THEATRE_STAGE`    | Appends a one-off stage            |

### A note on the port

Theatre binds **7427**. It was 7426 for about a day, chosen as the next free
seat in the `seren/port-map` fact — which lists the eight network services and
therefore doesn't list SerenSymposium, which binds 7426 on loopback for its UI
shim. Localhost-only is exactly why it wasn't in the map and exactly why it
collided. Symposium keeps the seat because Symposium is the one already
installed and running; the unshipped service moves.

Worth knowing if you're adding a service of your own: a single-source-of-truth
check inside one package can't catch this, because nothing drifted. Two
constants in two packages were both correct and identical, and identical is the
bug. Check the map, and make sure the map lists everything that *binds*, not
everything that's *reachable*.

---

## Stagehand — the half that does the work

```bash
pip install 'seren-theatre[stagehand]'
seren-theatre-stagehand recipe.yaml
```

> *"Stagehand does the work, cause they do fuckin everything, and the theatre
> shows the data."*

It is a **command first**, and Backstage is a front on that command, not a
second way to build. Starting a build is a write, so the plain viewer cannot do
it: no route, no button, and `test_the_base_install_has_no_write_verbs` fails
if one appears. Installing `[stagehand]` is what mounts Backstage, and it is
gated on importing the builder itself — the extra adds a dependency, not a
file, so the import is the assertion. What Backstage's *run* does is exactly
what the command does: fork the same argv, in a configured stage, with the log
landing in the run. If the theatre could build in its own process, the theatre
would be doing the work. **A stagehand is not on stage.**

What stagehand runs is the literal command from ms-moe-maker's README:

```
stagehand → /usr/local/bin/ms-moe-maker build /path/recipe.yaml --json
```

Not an import of `ms_moe_maker.runner`, not a Python API with its own defaults. The
same string a person types — so every automated run is also a test of the
documented one. If those ever diverged, the hand-run path is the one that would
rot, because it's the one with no users.

Forking is also what keeps torch out of the viewer's process. Theatre stays
installable and runnable on a box with no CUDA, because watching a run costs
nothing and that's the bargain.

```bash
seren-theatre-stagehand --check          # is it usable? which command will run?
seren-theatre-stagehand r.yaml -- --dryrun     # after -- goes to the builder
```

`--check` reports `is_documented_command`. If `ms-moe-maker` isn't on PATH, stagehand
falls back to `python -m ms_moe_maker` — that works, but it quietly voids the
"every run tests the hand-run path" guarantee, so it's reported rather than
hidden.

The plain service can *say* whether a stagehand is installed (`stagehand` on
`/`) and can never *use* it. Only the `[stagehand]` install can, and it says so
too: `backstage: true` and a non-empty `write_routes` on the same document.
Which room you are standing in is never a thing you have to infer.

---

## Development

```bash
pip install -e ".[test]"
pytest
```

The test suite includes a cross-check that the shell installer's `--describe`
and the Python module's `--describe` report the same name, port, group and
accent. Both exist on purpose — Starwright asks the installer, an operator asks
the service — and two sources that can disagree are only useful if something
compares them. Something now does.

---

## Licence

GPL-3.0-only. Same as the rest of the family.

*Rip it and win.* 🌭🔧
