"""Framework adapters over the producer SDK.

The adapters lazy-import their frameworks (stable-baselines3, lightning) so
the watchdog layer stays stdlib-only and installs cleanly everywhere.  The
metric-forwarding logic itself is framework-free (``MetricForwarder``) and is
what tests exercise; each adapter is a thin shell around it.
"""
from .forwarding import MetricForwarder
from .lightning import build_lightning_callback, sentinel_lightning_callback
from .sb3 import build_sentinel_callback, sentinel_callback

__all__ = ["MetricForwarder", "build_lightning_callback", "build_sentinel_callback",
           "sentinel_callback", "sentinel_lightning_callback"]
