"""Portable future-mutation contract for point-in-time visibility functions.

This module has no dependency on RL-Sentinel data or packages.  Any project
with an ``as-of`` visibility function can import it and prove that appending
records which were not yet available cannot change a past decision's inputs.

Typical use::

    from contracts.time_isolation import check_no_future_leak

    before_events = load_events_up_to(cutoff)
    future_events = [Event(... available_at > cutoff ...)]
    report = check_no_future_leak(visible, before_events, cutoff, future_events)
    assert report["ok"], report
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

# TypeVar (not PEP 695 ``def f[T]``) keeps this contract importable by projects
# that have not yet moved to Python 3.12 syntax.  The host project itself
# requires >=3.12; portability is the point of this module.
T = TypeVar("T")
K = TypeVar("K")


def check_no_future_leak(  # noqa: UP047 - keep the 3.11-compatible signature
    visible: Callable[[Sequence[T], K], Any],
    events: Sequence[T],
    cutoff: K,
    future_events: Sequence[T],
) -> dict[str, Any]:
    """Return a report; ``ok`` is True only if future records change nothing.

    The contract is deliberately strict: it compares the full serialized
    input of a decision, not just a few metrics.  A function that passes this
    check cannot silently consume a label, a revised sample or a later run.
    """
    before = visible(events, cutoff)
    after = visible(list(events) + list(future_events), cutoff)
    same = before == after
    return {
        "ok": bool(same),
        "cutoff": repr(cutoff),
        "n_events": len(events),
        "n_future_events": len(future_events),
        "detail": ("visibility unchanged after future mutation" if same
                   else "future mutation changed the as-of input"),
    }


def check_label_isolation(  # noqa: UP047 - keep the 3.11-compatible signature
    visible: Callable[[Sequence[T], K], Any],
    events: Sequence[T],
    cutoff: K,
    late_label: T,
    late_label_with_different_verdict: T,
) -> dict[str, Any]:
    """A decision's input must not depend on a label that arrives later.

    Two mutually contradictory future labels are appended in turn.  If either
    changes the as-of input, or if the two results differ from each other,
    the visibility function is leaking outcome information.
    """
    baseline = visible(events, cutoff)
    with_pass = visible(list(events) + [late_label], cutoff)
    with_fail = visible(list(events) + [late_label_with_different_verdict], cutoff)
    ok = baseline == with_pass == with_fail
    return {
        "ok": bool(ok),
        "detail": ("label arrival does not affect as-of input" if ok
                   else "as-of input depends on a future/revised label"),
    }
