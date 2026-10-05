"""Versioned, immutable event contracts. No retrospective timestamp inference."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Literal, Mapping, cast

Kind = Literal["start", "sample", "finish", "label"]
Origin = Literal["observed", "synthetic", "controlled"]
Scalar = str | int | float | bool | None


class ContractError(ValueError):
    """An input cannot support the declared replay contract."""


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError("expected a JSON object")
    result = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in result):
        raise ContractError("object keys must be strings")
    return cast(dict[str, object], result)


def text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ContractError(f"{field}: expected a nonempty string up to 256 characters")
    if any(ord(ch) < 32 for ch in value):
        raise ContractError(f"{field}: control characters are forbidden")
    return value


def number(value: object, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{field}: expected a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ContractError(f"{field}: number too large") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ContractError(f"{field}: invalid numeric range")
    return result


def integer(value: object, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{field}: expected an integer >= {minimum}")
    return value


def parse_json(value: str) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ContractError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    def invalid_constant(value: str) -> object:
        raise ContractError(f"non-finite JSON constant: {value}")

    return cast(object, json.loads(value, object_pairs_hook=unique,
                                   parse_constant=invalid_constant))


@dataclass(frozen=True)
class Event:
    event_id: str
    run_id: str
    kind: Kind
    event_time: float
    available_at: float
    fields: tuple[tuple[str, Scalar], ...]

    @classmethod
    def from_dict(cls, raw: object) -> Event:
        obj = mapping(raw)
        required = {"schema_version", "event_id", "run_id", "kind", "event_time",
                    "available_at", "payload"}
        if set(obj) != required or integer(obj.get("schema_version"), "schema_version") != 2:
            raise ContractError("event requires exactly the schema-v2 fields")
        event_id = text(obj["event_id"], "event_id")
        run_id = text(obj["run_id"], "run_id")
        kind = text(obj["kind"], "kind")
        if kind not in ("start", "sample", "finish", "label"):
            raise ContractError("unknown event kind")
        event_time = number(obj["event_time"], "event_time", 0)
        available_at = number(obj["available_at"], "available_at", event_time)
        payload = mapping(obj["payload"])
        clean: dict[str, Scalar] = {}
        if kind == "start":
            if set(payload) != {"task", "seed", "planned_steps"}:
                raise ContractError("start payload requires task, seed, planned_steps")
            clean = {"task": text(payload["task"], "task"),
                     "seed": text(payload["seed"], "seed"),
                     "planned_steps": integer(payload["planned_steps"], "planned_steps", 1)}
        elif kind == "sample":
            allowed = {"step", "reward", "approx_kl", "value_loss", "memory_percent"}
            if "step" not in payload or not set(payload) <= allowed:
                raise ContractError("sample payload contains unsupported or missing fields")
            clean["step"] = integer(payload["step"], "step")
            for key in ("reward", "approx_kl", "value_loss", "memory_percent"):
                if key in payload and payload[key] is not None:
                    clean[key] = number(payload[key], key, None if key == "reward" else 0)
            if "memory_percent" in clean and number(clean["memory_percent"], "memory_percent") > 100:
                raise ContractError("memory_percent must not exceed 100")
        elif kind == "finish":
            if set(payload) != {"status"} or payload["status"] not in ("completed", "failed", "cancelled"):
                raise ContractError("finish requires a valid status")
            clean["status"] = cast(str, payload["status"])
        else:
            if set(payload) != {"verdict", "source"}:
                raise ContractError("label requires verdict and source")
            if payload["verdict"] not in ("pass", "fail", "unknown") or payload["source"] not in ("manual", "evaluation"):
                raise ContractError("invalid label verdict or source")
            clean = {"verdict": cast(str, payload["verdict"]), "source": cast(str, payload["source"])}
        return cls(event_id, run_id, cast(Kind, kind), event_time, available_at,
                   tuple(sorted(clean.items())))

    def payload(self) -> dict[str, Scalar]:
        return dict(self.fields)

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 2, "event_id": self.event_id, "run_id": self.run_id,
                "kind": self.kind, "event_time": self.event_time,
                "available_at": self.available_at, "payload": self.payload()}


def make_event(event_id: str, run_id: str, kind: Kind, event_time: float,
               available_at: float, payload: Mapping[str, Scalar]) -> Event:
    return Event.from_dict({"schema_version": 2, "event_id": event_id, "run_id": run_id,
                           "kind": kind, "event_time": event_time,
                           "available_at": available_at, "payload": dict(payload)})


def validate_events(events: tuple[Event, ...]) -> tuple[Event, ...]:
    """Validate a complete immutable revision, independent of input row order."""
    ids: set[str] = set()
    groups: dict[str, list[Event]] = {}
    for item in events:
        clean = Event.from_dict(item.to_dict())
        if clean != item or len(item.fields) != len(dict(item.fields)):
            raise ContractError("event must use canonical immutable fields")
        if item.event_id in ids:
            raise ContractError(f"duplicate event_id: {item.event_id}")
        ids.add(item.event_id)
        groups.setdefault(item.run_id, []).append(item)
    for run_id, records in groups.items():
        starts = [e for e in records if e.kind == "start"]
        finishes = [e for e in records if e.kind == "finish"]
        if len(starts) != 1 or len(finishes) > 1:
            raise ContractError(f"{run_id}: requires one start and at most one finish; retries need new run_id")
        start = starts[0]
        for e in records:
            if e.event_time < start.event_time or e.available_at < start.available_at:
                raise ContractError(f"{run_id}: event precedes observable start")
            if e.kind == "label" and (not finishes or e.event_time < finishes[0].event_time
                                      or e.available_at < finishes[0].available_at):
                raise ContractError(f"{run_id}: label precedes observable finish")
            if e.kind == "sample" and finishes and e.event_time > finishes[0].event_time:
                raise ContractError(f"{run_id}: sample occurred after finish")
        samples = sorted((e for e in records if e.kind == "sample"),
                         key=lambda e: (e.event_time, e.event_id))
        steps = [integer(e.payload()["step"], "step") for e in samples]
        if any(b <= a for a, b in zip(steps, steps[1:])):
            raise ContractError(f"{run_id}: sample steps must strictly increase in event time; new attempt needs new run_id")
        labels = [e for e in records if e.kind == "label"]
        keys = [(e.available_at, e.payload()["source"]) for e in labels]
        if len(keys) != len(set(keys)):
            raise ContractError(f"{run_id}: ambiguous simultaneous labels from the same source")
    return tuple(sorted(events, key=lambda e: (e.available_at, e.event_time, e.event_id)))
