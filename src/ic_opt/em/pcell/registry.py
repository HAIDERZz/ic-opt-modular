from __future__ import annotations

import hashlib
import importlib.resources
import importlib.util
import sys
import threading
from pathlib import Path

from ic_opt.em.pcell.base import PassiveDeviceGenerator

# No generators ship as hard-coded built-ins any more (M12: the legacy
# single_turn_transformer and center_tapped_symmetric_inductor generators
# were removed; center_tapped_symmetric_inductor's broken geometry caused the
# 2026-07-09 server incident). The clean-port family
# (`plugin_module: "builtin:clean_port"`) is the device library going
# forward, loaded via the plugin mechanism below like any other plugin.
_BUILTIN_IDS: tuple[str, ...] = ()

_PLUGIN_LOAD_LOCK = threading.Lock()

# `plugin_module: "builtin:<name>"` resolution (M11 R2): maps a short,
# install-location-independent name to the plugin file shipped inside this
# package, so contracts no longer need an absolute path to the device
# library. Add new entries here as more device families ship in-package.
_BUILTIN_PLUGIN_MODULES = {
    "clean_port": ("ic_opt.em.pcell", "generator_plugin.py"),
}

_BUILTIN_PREFIX = "builtin:"


def resolve_plugin_module_path(module_path: str | Path) -> Path:
    """Resolve a contract `plugin_module` value to a concrete file path.

    Absolute paths pass through unchanged. ``"builtin:<name>"`` resolves to
    the installed plugin file for that builtin device library; an unknown
    name fails closed with the list of available builtins.
    """
    if isinstance(module_path, str) and module_path.startswith(_BUILTIN_PREFIX):
        name = module_path[len(_BUILTIN_PREFIX):]
        target = _BUILTIN_PLUGIN_MODULES.get(name)
        if target is None:
            raise ValueError(
                f"unknown builtin plugin module: {name!r}; "
                f"available builtins: {sorted(_BUILTIN_PLUGIN_MODULES)}")
        package, filename = target
        return Path(str(importlib.resources.files(package) / filename))
    return Path(module_path)


def _load_plugin_module(module_path: str | Path):
    """Load (and per-process cache) a contract-declared plugin module."""
    path = resolve_plugin_module_path(module_path)
    if not path.is_absolute():
        raise ValueError(f"geometry plugin module path must be absolute: {path}")
    if not path.is_file():
        raise ValueError(f"geometry plugin module not found: {path}")
    module_name = "_geometry_plugin_" + hashlib.sha256(
        str(path.resolve()).encode()
    ).hexdigest()[:16]
    with _PLUGIN_LOAD_LOCK:
        if module_name in sys.modules:
            return sys.modules[module_name], path
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"geometry plugin module cannot be loaded: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            del sys.modules[module_name]
            raise ValueError(
                f"geometry plugin module failed to import: {path}: {exc}"
            ) from exc
        return module, path


def load_plugin_retired_ids(module_path: str | Path) -> dict[str, str]:
    """The plugin's optional RETIRED_GENERATOR_IDS map (id -> migration
    recipe); empty when the plugin does not declare one."""
    module, path = _load_plugin_module(module_path)
    retired = getattr(module, "RETIRED_GENERATOR_IDS", {})
    if not isinstance(retired, dict):
        raise ValueError(
            f"geometry plugin RETIRED_GENERATOR_IDS must be a dict: {path}")
    return dict(retired)


def load_plugin_generators(module_path: str | Path) -> dict[str, PassiveDeviceGenerator]:
    """Load PLUGIN_GENERATORS from a contract-declared plugin file, fail-closed.

    Exported generator instances are shared across calls and must stay
    stateless per generate(); the module is cached per-process (restart to
    pick up plugin edits). ``module_path`` may be an absolute path or a
    ``"builtin:<name>"`` reference (see ``resolve_plugin_module_path``).
    """
    module, path = _load_plugin_module(module_path)
    generators = getattr(module, "PLUGIN_GENERATORS", None)
    if not isinstance(generators, dict):
        raise ValueError(
            f"geometry plugin module must export PLUGIN_GENERATORS dict: {path}")
    for key, generator in generators.items():
        if not isinstance(generator, PassiveDeviceGenerator):
            raise ValueError(
                f"plugin generator {key!r} is not a PassiveDeviceGenerator: {path}")
        if generator.generator_id != key:
            raise ValueError(
                f"plugin dict key {key!r} does not match generator_id "
                f"{generator.generator_id!r}: {path}")
        if key in _BUILTIN_IDS:
            raise ValueError(
                f"plugin generator id {key!r} collides with a built-in generator: {path}")
    return dict(generators)


def get_generator(
    generator_id: str,
    *,
    plugin_module: str | Path | None = None,
) -> PassiveDeviceGenerator:
    # There are no hard-coded built-in generators; every device (including
    # the in-package clean-port family) is loaded through the plugin
    # mechanism. Declare `plugin_module: "builtin:clean_port"` (or an
    # absolute path to a custom plugin file) in the contract.
    if plugin_module is not None:
        plugins = load_plugin_generators(plugin_module)
        if generator_id in plugins:
            return plugins[generator_id]
        retired = load_plugin_retired_ids(plugin_module)
        if generator_id in retired:
            raise ValueError(
                f"retired geometry generator: {generator_id}; "
                f"{retired[generator_id]}")
        raise ValueError(
            f"unknown geometry generator: {generator_id}; "
            f"plugin ids: {sorted(plugins)}; "
            "see the plugin mechanism (plugin_module: \"builtin:clean_port\")")
    raise ValueError(
        f"unknown geometry generator: {generator_id}; no plugin_module was "
        "given. Declare plugin_module: \"builtin:clean_port\" (or an "
        "absolute path to a custom plugin file) in the contract.")
