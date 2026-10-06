"""Executable tabular Q-learning fault-injection experiment, not Go2W validation."""
from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .engine import ConstantPolicy, Predictor, RulePolicy, Timeline
from .events import canonical
from .metrics import summarize
from .reporting import render_html, runtime_info
from .storage import EventStore, encode_events, publish_bundle

Fault = Literal["none", "learning_disabled", "late_reset"]
FAULTS: tuple[Fault, ...] = ("none", "learning_disabled", "late_reset")


def transition(state: int, action: int) -> tuple[int, float, bool]:
    if state not in range(16) or action not in range(4):
        raise ValueError("invalid grid state/action")
    if state == 15:
        return 15, 0.0, True
    x, y = state % 4, state // 4
    dx, dy = ((1, 0), (0, 1), (-1, 0), (0, -1))[action]
    nx, ny = x + dx, y + dy
    if not (0 <= nx < 4 and 0 <= ny < 4) or (nx, ny) in ((1, 1), (1, 2)):
        nx, ny = x, y
    nxt = ny * 4 + nx
    return nxt, 1.0 if nxt == 15 else -0.01, nxt == 15


def greedy(q: list[list[float]], state: int, rng: random.Random) -> int:
    best = max(q[state])
    return rng.choice([a for a in range(4) if q[state][a] == best])


@dataclass(frozen=True)
class Rollout:
    reward: float
    steps: int
    success: bool

    def to_dict(self) -> dict[str, object]:
        return {"reward": self.reward, "steps": self.steps, "success": self.success}


def evaluate(q: list[list[float]], seed: int, episodes: int = 30) -> tuple[Rollout, ...]:
    rng = random.Random(seed)
    result: list[Rollout] = []
    for _ in range(episodes):
        state, reward, done = 0, 0.0, False
        steps = 0
        for steps in range(1, 51):
            state, r, done = transition(state, greedy(q, state, rng))
            reward += r
            if done:
                break
        result.append(Rollout(reward, steps, done))
    return tuple(result)


def train_trial(seed: int, fault: Fault, observe: Callable[[int, float], None],
                budget: int = 4000, stride: int = 250) -> tuple[list[list[float]], tuple[Rollout, ...]]:
    if budget < 1000 or stride < 1 or budget % stride or fault not in FAULTS:
        raise ValueError("invalid controlled protocol")
    rng = random.Random(seed)
    q = [[0.0] * 4 for _ in range(16)]
    state, episode_steps = 0, 0
    frozen = fault == "learning_disabled"
    for step in range(1, budget + 1):
        if fault == "late_reset" and step == int(budget * 0.55):
            q = [[0.0] * 4 for _ in range(16)]
            frozen = True
        action = rng.randrange(4) if rng.random() < 0.2 else greedy(q, state, rng)
        nxt, reward, done = transition(state, action)
        if not frozen:
            target = reward + (0.0 if done else 0.95 * max(q[nxt]))
            q[state][action] += 0.3 * (target - q[state][action])
        state, episode_steps = nxt, episode_steps + 1
        if done or episode_steps >= 50:
            state, episode_steps = 0, 0
        if step % stride == 0:
            rolls = evaluate(q, 7919 + seed, episodes=20)
            observe(step, sum(r.reward for r in rolls) / len(rolls))
    # Different RNG seeds from training and monitoring, but the same toy scene.
    return q, evaluate(q, 99173 + seed)


def experiment(destination: Path, seeds: int = 6) -> dict[str, object]:
    if not 1 <= seeds <= 100:
        raise ValueError("seeds must be in 1..100")
    protocol: dict[str, object] = {
        "experiment": "tabular-q-learning-controlled-fault-injection-v1",
        "origin": "controlled", "training_seeds": list(range(seeds)), "faults": list(FAULTS),
        "environment": "4x4 grid; start=0; goal=15; blocked cells=(1,1),(1,2)",
        "budget_steps": 4000, "sample_stride": 250, "learning_rate": 0.3,
        "discount": 0.95, "epsilon": 0.2, "episode_limit": 50,
        "label_rule": "pass iff final held-out-RNG evaluation success rate >= 0.9 over 30 episodes",
        "warning": "Controlled CPU experiment in one toy scene. Fault labels/conditions are evaluation-only. Not a real-world efficacy benchmark.",
    }
    trials: list[dict[str, object]] = []
    weights: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="rl-risk-controlled-") as tmp:
        store = EventStore.create(Path(tmp) / "events.sqlite", origin="controlled")
        for fault_index, fault in enumerate(FAULTS):
            for seed in range(seeds):
                run_id = f"trial-{fault_index * seeds + seed:03d}"
                store.record(run_id, "start", {"task": "gridworld4x4", "seed": str(seed), "planned_steps": 4000})

                def observe(step: int, reward: float) -> None:
                    store.record(run_id, "sample", {"step": step, "reward": reward})

                q, rollouts = train_trial(seed, fault, observe)
                store.record(run_id, "finish", {"status": "completed"})
                success = sum(r.success for r in rollouts) / len(rollouts)
                verdict = "pass" if success >= 0.9 else "fail"
                store.record(run_id, "label", {"verdict": verdict, "source": "evaluation"})
                weights[run_id] = q
                trials.append({"run_id": run_id, "fault": fault, "seed": seed,
                               "success_rate": success, "verdict": verdict,
                               "final_evaluation": [r.to_dict() for r in rollouts]})
        events, origin = store.read()
    timeline = Timeline(events)
    summaries: list[dict[str, object]] = []
    decisions: list[dict[str, object]] = []
    policies: tuple[Predictor, ...] = (RulePolicy(), ConstantPolicy(False), ConstantPolicy(True))
    for policy in policies:
        for progress in (0.3, 0.5, 0.7):
            rows = timeline.replay(policy, progress)
            summaries.append({"policy": policy.name, "progress": progress, **summarize(rows, origin)})
            decisions.extend(r.to_dict() for r in rows)
    result: dict[str, object] = {"title": "受控 RL 故障注入实验", "protocol": protocol,
                                "runtime": runtime_info(), "trials": trials,
                                "summaries": summaries, "decisions": decisions}
    publish_bundle(destination, {"events.jsonl": encode_events(events, origin),
                                 "results.json": canonical(result) + "\n",
                                 "protocol.json": canonical(protocol) + "\n",
                                 "q_tables.json": canonical(weights) + "\n",
                                 "report.html": render_html(result)})
    return {"output": str(destination), "trials": len(trials),
            "passes": sum(t["verdict"] == "pass" for t in trials),
            "failures": sum(t["verdict"] == "fail" for t in trials),
            "summaries": summaries}
