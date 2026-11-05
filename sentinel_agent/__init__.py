"""Staleness-aware training watchdog layer.

New layer (v0.4 direction): a producer SDK and a live watchdog agent built on
top of the frozen strict core.  This package deliberately does not modify
``rl_risk_replay/``; every event still passes the schema-v2 contract, and the
agent refuses to monitor anything the contract does not vouch for.
"""
__version__ = "0.4.0"

from .agent import Agent, AgentConfig, RunState
from .producer import SAMPLE_METRICS, SentinelProducer, torchrun_run_id

__all__ = [
           "SAMPLE_METRICS",
           "Agent",
           "AgentConfig",
           "RunState",
           "SentinelProducer",
           "__version__",
           "torchrun_run_id",
]
