"""Passive-device geometry library (six clean-port families, process rule profiles, DRC audit).

Moved from em-opt ``devices/clean_port`` + ``geometry`` as-is: the value is in
the per-family pydantic configs and the construction code. ``get_generator``
resolves ``builtin:clean_port`` (this package) or an external plugin file.
"""

from ic_opt.em.pcell.base import GeometryGenerationResult, PassiveDeviceGenerator
from ic_opt.em.pcell.registry import get_generator, resolve_plugin_module_path

# The generation of the built-in families' default geometry. Bump it when a
# change makes any existing configuration's GDS physically different (the
# gds_compare sense): the generators stamp it into geometry_manifest.json and
# the Pcell stage carries it in the pipeline fingerprint, so observations of an
# older generation are never reused for a newer one. 6 = em-opt's
# ingest.CURRENT_GEOM_VERSION at the move (port contract + taps + landing fixes);
# 7 = the M1 primitive convergence (wide-parallel rules judged on the drawn
# diagonal width, single chamfer source, landing guards).
GEOMETRY_VERSION = 7

__all__ = [
    "GEOMETRY_VERSION",
    "GeometryGenerationResult",
    "PassiveDeviceGenerator",
    "Report",
    "Violation",
    "audit_gds",
    "canonical_conductor",
    "get_generator",
    "product_scope_record",
    "require_layers_from_config",
    "resolve_plugin_module_path",
]

# drc_audit imports klayout.db at module scope; keep it lazy so klayout-less
# environments can still resolve generators (PEP 562).
_DRC_AUDIT_NAMES = frozenset(("Report", "Violation", "audit_gds", "canonical_conductor", "product_scope_record", "require_layers_from_config"))


def __getattr__(name: str):
    if name in _DRC_AUDIT_NAMES:
        from ic_opt.em.pcell import drc_audit

        return getattr(drc_audit, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
