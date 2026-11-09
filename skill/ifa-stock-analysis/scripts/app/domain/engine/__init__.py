"""
Engine registry — pick the active DEEP version by name.

Upgrade path: add a module with `class DeepV9Engine(DeepEngine)`, register it here,
and set DEEP_VERSION in config.py. Nothing else changes. An unknown version raises
instead of silently falling back to an older engine.
"""
from .contract import DeepEngine, Valuation
from .deep_v82 import DeepV82Engine

_ENGINES = {}


def register(engine):
    _ENGINES[engine.version] = engine


register(DeepV82Engine())


def get_engine(version=None):
    if version is None:
        import config
        version = config.DEEP_VERSION
    try:
        return _ENGINES[version]
    except KeyError:
        raise ValueError(f"unknown DEEP engine version {version!r}; "
                         f"available: {available_versions()}") from None


def available_versions():
    return sorted(_ENGINES.keys())
