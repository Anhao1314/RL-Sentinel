"""As-of inputs, frozen historical membership, and outcome-separated evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Protocol

from .events import (ContractError, Event, canonical, digest, integer, number,
                     validate_events)
from .policy import STRICT_POLICY_NAME, STRICT_RULE_ID, StrictRuleConfig as RuleConfig

Action = Literal["continue", "watch", "stop", "tune", "resize", "abstain"]


@dataclass(frozen=True)
class HistoricalRun:
    start: Event
    samples: tuple[Event, ...]
    finish: Event
    label: Event

    def to_dict(self) -> dict[str, object]:
        return {"start": self.start.to_dict(), "samples": [e.to_dict() for e in self.samples],
                "finish": self.finish.to_dict(), "label": self.label.to_dict()}


@dataclass(frozen=True)
class View:
    cutoff: float
    start: Event
    samples: tuple[Event, ...]
    history: tuple[HistoricalRun, ...]

    def to_dict(self) -> dict[str, object]:
        return {"cutoff": self.cutoff, "start": self.start.to_dict(),
                "samples": [e.to_dict() for e in self.samples],
                "history": [r.to_dict() for r in self.history]}

    @property
    def sha256(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class Decision:
    action: Action
    level: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.action not in ("continue", "watch", "stop", "tune", "resize", "abstain"):
            raise ContractError("invalid recommendation action")
        integer(self.level, "decision level")
        if self.level not in range(4) or not self.reasons:
            raise ContractError("decision requires level 0..3 and evidence reasons")

    def to_dict(self) -> dict[str, object]:
        return {"action": self.action, "risk_level": f"R{self.level}",
                "reasons": list(self.reasons), "recommendation_only": True,
                "calibrated_probability": None}


class Predictor(Protocol):
    @property
    def name(self) -> str: ...

    def __call__(self, view: View) -> Decision: ...


@dataclass(frozen=True)
class RulePolicy:
    """Versioned safe-input subset, not a parity claim with all legacy risk rules.

    The display name is kept as ``rules-v2`` for reproducible outputs; the
    machine-readable identity is ``rule_id``.
    """
    config: RuleConfig = RuleConfig()
    name: str = STRICT_POLICY_NAME

    @property
    def rule_id(self) -> str:
        return STRICT_RULE_ID

    def __call__(self, view: View) -> Decision:
        rewards = [number(e.payload()["reward"], "reward") for e in view.samples
                   if e.payload().get("reward") is not None]
        kl = [number(e.payload()["approx_kl"], "approx_kl") for e in view.samples
              if e.payload().get("approx_kl") is not None]
        mem = [number(e.payload()["memory_percent"], "memory_percent") for e in view.samples
               if e.payload().get("memory_percent") is not None]
        reasons: list[str] = []
        if len(rewards) >= self.config.min_reward_points:
            peak = max(rewards)
            # Relative drawdown is deliberately undefined for a non-positive peak.
            # Negative/near-zero reward tasks need a task-specific policy.
            if peak > 1e-8:
                drawdown = (peak - rewards[-1]) / peak
                if drawdown >= self.config.drawdown_stop:
                    return Decision("stop", 2, (f"drawdown={drawdown:.6g} >= {self.config.drawdown_stop}",))
            else:
                reasons.append("drawdown unavailable: non-positive reward peak")
        if mem and mem[-1] >= self.config.memory_resize:
            return Decision("resize", 2, (f"memory_percent={mem[-1]:.6g}",))
        if kl and kl[-1] >= self.config.kl_tune:
            return Decision("tune", 2, (f"approx_kl={kl[-1]:.6g}",))
        if kl and kl[-1] >= self.config.kl_watch:
            return Decision("watch", 1, (f"approx_kl={kl[-1]:.6g}",))
        if len(rewards) < self.config.min_reward_points:
            return Decision("abstain", 0, ("insufficient reward observations",))
        if reasons:
            return Decision("abstain", 0, tuple(reasons))
        return Decision("continue", 0, ("no configured risk threshold crossed; not proof of training health",))


@dataclass(frozen=True)
class ConstantPolicy:
    stop: bool = False

    @property
    def name(self) -> str:
        return "always-stop" if self.stop else "always-continue"

    @property
    def rule_id(self) -> str:
        return f"constant/{self.name}@1"

    def __call__(self, view: View) -> Decision:
        return Decision("stop" if self.stop else "continue", 0,
                        ("constant control baseline, not a risk estimate",))


@dataclass(frozen=True)
class Row:
    run_id: str
    policy: str
    progress: float
    status: str
    reason: str
    view: View | None
    decision: Decision | None
    verdict: str
    finish_time: float | None

    def to_dict(self, *, include_input: bool = True) -> dict[str, object]:
        """Serialize a decision row.

        Full as-of views are large and repeat across policies/checkpoints, so
        callers publishing a bundle should set ``include_input=False`` and emit
        the deduplicated views via :func:`encode_views` instead.
        """
        result: dict[str, object] = {
            "run_id": self.run_id, "policy": self.policy, "progress": self.progress,
            "status": self.status, "reason": self.reason,
            "input_sha256": self.view.sha256 if self.view else None,
        }
        if include_input:
            result["input"] = self.view.to_dict() if self.view else None
        result["decision"] = self.decision.to_dict() if self.decision else None
        result["evaluation_only"] = {"verdict": self.verdict,
                                     "finish_time": self.finish_time}
        return result


def encode_views(rows: Iterable[Row]) -> str:
    """Deduplicated JSONL of as-of views, keyed by content hash.

    Keeping views in one member instead of embedding each view in every
    decision removes the quadratic growth of evidence bundles while preserving
    byte-for-byte reproducibility of every policy input.
    """
    seen: dict[str, dict[str, object]] = {}
    for row in rows:
        if row.view is None:
            continue
        key = row.view.sha256
        if key not in seen:
            seen[key] = {"input_sha256": key, "input": row.view.to_dict()}
    return "".join(canonical(seen[key]) + "\n" for key in sorted(seen))


class Timeline:
    def __init__(self, events: tuple[Event, ...]) -> None:
        self.events = validate_events(events)
        groups: dict[str, list[Event]] = {}
        for e in self.events:
            groups.setdefault(e.run_id, []).append(e)
        self.groups = {key: tuple(value) for key, value in groups.items()}
        self.starts = sorted((e for e in self.events if e.kind == "start"),
                             key=lambda e: (e.event_time, e.run_id))

    def visible(self, run_id: str, cutoff: float) -> tuple[Event, ...]:
        number(cutoff, "cutoff", 0)
        return tuple(e for e in self.groups.get(run_id, ())
                     if e.event_time <= cutoff and e.available_at <= cutoff)

    def label(self, run_id: str, cutoff: float) -> Event | None:
        labels = [e for e in self.visible(run_id, cutoff) if e.kind == "label"]
        # Manual labels override automatic labels only after they become observable.
        return max(labels, key=lambda e: (e.payload()["source"] == "manual", e.available_at,
                                           e.event_id), default=None)

    def as_of(self, run_id: str, cutoff: float) -> View:
        visible = self.visible(run_id, cutoff)
        starts = [e for e in visible if e.kind == "start"]
        if not starts:
            raise ContractError("target start is not observable at cutoff")
        start = starts[0]
        samples = tuple(sorted((e for e in visible if e.kind == "sample"),
                               key=lambda e: (e.event_time, e.event_id)))
        history: list[HistoricalRun] = []
        # Freeze training membership at target start, not at eventual decision time.
        history_cutoff = start.event_time
        for other in self.starts:
            if other.run_id == run_id or other.available_at >= history_cutoff:
                continue
            prior = tuple(e for e in self.visible(other.run_id, history_cutoff)
                          if e.available_at < history_cutoff)
            finishes = [e for e in prior if e.kind == "finish"]
            labels = [e for e in prior if e.kind == "label"]
            label = max(labels, key=lambda e: (e.payload()["source"] == "manual", e.available_at,
                                               e.event_id), default=None)
            if not finishes or label is None or label.payload()["verdict"] == "unknown":
                continue
            prior_samples = tuple(sorted((e for e in prior if e.kind == "sample"),
                                         key=lambda e: (e.event_time, e.event_id)))
            history.append(HistoricalRun(other, prior_samples, finishes[0], label))
        return View(cutoff, start, samples, tuple(history))

    def replay(self, policy: Predictor, progress: float = 0.5,
               evaluation_at: float | None = None) -> tuple[Row, ...]:
        progress = number(progress, "progress", 0)
        if not 0 < progress <= 1:
            raise ContractError("progress must be in (0,1]")
        horizon = max((e.available_at for e in self.events), default=0.0) if evaluation_at is None else number(evaluation_at, "evaluation_at", 0)
        rows: list[Row] = []
        for start in self.starts:
            if start.available_at > horizon:
                continue
            visible = self.visible(start.run_id, horizon)
            budget = integer(start.payload()["planned_steps"], "planned_steps", 1)
            candidates = [e for e in visible if e.kind == "sample"
                          and integer(e.payload()["step"], "step") >= progress * budget]
            cutoff = min((e.available_at for e in candidates), default=None)
            reason = "progress not observable by evaluation horizon"
            view: View | None = None
            decision: Decision | None = None
            status = "skipped"
            if cutoff is not None:
                if any(e.kind == "finish" for e in self.visible(start.run_id, cutoff)):
                    reason = "finish already observable at decision checkpoint"
                else:
                    view = self.as_of(start.run_id, cutoff)
                    decision = policy(view)
                    status = "abstained" if decision.action == "abstain" else "decided"
                    reason = "; ".join(decision.reasons)
            # Outcomes are accessed only after the predictor returns.
            label = self.label(start.run_id, horizon)
            finishes = [e for e in visible if e.kind == "finish"]
            rows.append(Row(start.run_id, policy.name, progress, status, reason, view, decision,
                            str(label.payload()["verdict"]) if label else "unknown",
                            finishes[0].event_time if finishes else None))
        return tuple(rows)
