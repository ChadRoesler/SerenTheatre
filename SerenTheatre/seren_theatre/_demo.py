"""A rehearsal. Fabricated state so the room can be judged with nobody in it.

WHY THIS IS SAFE TO SHIP AND WHY IT IS LOUD ABOUT ITSELF
--------------------------------------------------------
A dashboard is a claim about reality, so a demo mode is a dashboard that lies
on purpose. That is fine for laying out a room and catastrophic if it is ever
mistaken for a reading - "is this the real run or the demo" is exactly the
question you do not want someone asking at 2am while a 14B trains.

So it is fenced three ways, deliberately over-engineered for how small it is:

  1. It only exists behind an explicit --demo flag. Never a config key, never
     an env var, never a fallback when a stage is missing. An empty stage shows
     an EMPTY room, because "nothing is happening" is a true and useful reading
     and inventing a run to fill the space would be the actual sin.
  2. Every fabricated stage is named with a DEMO prefix and every log with a
     .demo suffix, so the fiction survives being screenshotted, cropped, or
     pasted into a chat without the surrounding page.
  3. The state payload carries demo=true, and the viewer paints a banner from
     it - the marking travels with the DATA, not with the page that rendered it.

The numbers below are shaped like the real 14B run (34.79 s/it, ~0.18 loss on
agentcore, the 4.3x PowerShell/shell token skew) so the layout gets stress-
tested against realistic magnitudes rather than tidy round ones. They are still
made up.
"""
from __future__ import annotations

import time
from typing import Any, Dict


def demo_state(refresh: float, version: str) -> Dict[str, Any]:
    now = time.time()
    return {
        "generated": now,
        "took_ms": 0.0,
        "refresh_seconds": refresh,
        "version": version,
        # Travels with the data, not the page. See the docstring.
        "demo": True,
        "stages": [{
            "name": "DEMO · FraunkensteinsLab",
            "path": "(fabricated - no such directory)",
            "exists": True,
            "logs": [
                {
                    "name": "run14b.demo.log",
                    "path": "(fabricated)",
                    "mtime": now - 41, "size": 4_355_216,
                    "phase": "training specialist",
                    "activity": None,
                    "subject": "shell",
                    "cfg": {"size": "14B", "target_steps": "1200",
                            "batch": "4x2", "topk": "2"},
                    "budgets": [
                        {"expert": "powershell", "tokens_m": 19.66,
                         "budget_m": 19.66, "docs_used": 9860,
                         "docs_total": 12000, "steps": 1200, "short": False},
                        {"expert": "python", "tokens_m": 19.66,
                         "budget_m": 19.66, "docs_used": 21701,
                         "docs_total": 24000, "steps": 1200, "short": False},
                        {"expert": "csharp", "tokens_m": 19.66,
                         "budget_m": 19.66, "docs_used": 21918,
                         "docs_total": 24000, "steps": 1200, "short": False},
                        # The one that matters: a SHORT expert is a result, and
                        # it has to be legible at a glance or the whole
                        # token-budget change was for nothing.
                        {"expert": "shell", "tokens_m": 4.66,
                         "budget_m": 19.66, "docs_used": 10000,
                         "docs_total": 10000, "steps": 284, "short": True},
                    ],
                    "step": {"step": 657, "total": 1200, "loss": 0.1834,
                             "grad_norm": 0.01112, "lr": 2.748e-05,
                             "accuracy": 0.9378, "epoch": 0.873,
                             "tokens": 1.08e7, "rate": "34.79s/it",
                             "eta": "5:14:52"},
                    "milestones": ["saved qwen_coder_powershell",
                                   "saved qwen_coder_python",
                                   "saved qwen_coder_csharp"],
                    "warnings": ["an expert is short of budget"],
                    "stalled_for": 41.0,
                },
                {
                    "name": "run_0.5B.demo.log",
                    "path": "(fabricated)",
                    "mtime": now - 7_200, "size": 118_004,
                    "phase": "smoke test passed",
                    "activity": None, "subject": "agentcore",
                    "cfg": {"size": "0.5B", "target_steps": "150"},
                    "budgets": [],
                    "step": {"step": 152, "total": 152, "loss": 1.029,
                             "grad_norm": 0.0724, "lr": 5.882e-06,
                             "accuracy": 0.8149, "epoch": 1.0,
                             "tokens": 2.476e6, "rate": "3.86s/it",
                             "eta": "00:00"},
                    "milestones": ["skeleton saved", "router training",
                                   "MoE alive", "GGUF converted",
                                   "smoke test passed"],
                    "warnings": [],
                    "stalled_for": 7_200.0,
                },
            ],
            "rungs": [
                {"name": "dryrun_0.5B", "path": "(fabricated)",
                 "specialists": ["agentcore", "csharp", "powershell",
                                 "python", "shell"],
                 "experts": ["powershell", "python", "csharp", "shell",
                             "agentcore"],
                 "dense_layers": [], "skeleton": True, "final": True,
                 "gguf": {"name": "msmoe-0.5B-f16.gguf", "gb": 3.78},
                 "smoketested": True},
                {"name": "fraunkenstein_agent_14B", "path": "(fabricated)",
                 "specialists": ["csharp", "powershell", "python"],
                 "dense_layers": list(range(24)),
                 "skeleton": False, "final": False,
                 "gguf": None, "smoketested": False},
            ],
        }],
    }
