"""
Engine registry — pick the active DEEP version by name.

Upgrade path: add `deep_v74.py` with `class DeepV74Engine(DeepEngine)`, register
it here, and set DEEP_VERSION="7.4" in config.py. Nothing else changes.
"""
from .contract import DeepEngine, Valuation
from .deep_v73 import DeepV73Engine
from .deep_v82 import DeepV82Engine

_ENGINES = {}


def register(engine):
    _ENGINES[engine.version] = engine


register(DeepV73Engine())
register(DeepV82Engine())


def get_engine(version=None):
    if version is None:
        try:
            import config
            version = getattr(config, "DEEP_VERSION", "7.3")
        except Exception:
            version = "7.3"
    return _ENGINES.get(version) or _ENGINES["7.3"]


def available_versions():
    return sorted(_ENGINES.keys())
