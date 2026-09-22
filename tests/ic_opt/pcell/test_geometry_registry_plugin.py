"""Plugin loading in the geometry registry (fail-closed)."""
import pathlib
import textwrap
from pathlib import Path

import pytest

from ic_opt.em.pcell.registry import get_generator, load_plugin_generators
from tests.ic_opt.pcell.conftest import PACKAGE_DIR, requires_profile

pytestmark = requires_profile("n28_1p10m")
ROOT = pathlib.Path(__file__).resolve().parents[3]
REAL_PLUGIN = PACKAGE_DIR / "generator_plugin.py"


def test_load_real_plugin_and_resolve_generators():
    generators = load_plugin_generators(REAL_PLUGIN)
    assert set(generators) == {"clean_port_ind_sym", "clean_port_xfm_bs",
                               "clean_port_xfm_ms", "clean_port_xfm_balun",
                               "clean_port_xfm_tw", "clean_port_xfm_il"}
    gen = get_generator("clean_port_ind_sym", plugin_module=REAL_PLUGIN)
    assert gen.generator_id == "clean_port_ind_sym"


def test_retired_ind_sym_ct_resolution_fails_with_migration_mapping():
    # M13 ticket 03: the standalone CT inductor id was retired; resolution
    # must fail closed and carry the replacement recipe (unified id +
    # ct_metal field) so old requirement files migrate in one edit.
    with pytest.raises(ValueError, match="ct_metal"):
        get_generator("clean_port_ind_sym_ct", plugin_module=REAL_PLUGIN)
    with pytest.raises(ValueError, match="retired"):
        get_generator("clean_port_ind_sym_ct", plugin_module=REAL_PLUGIN)


def test_unknown_id_without_plugin_module_fails_closed_with_hint():
    # M12: there are no hard-coded built-ins any more; every device (including
    # the clean-port family) resolves only through a declared plugin_module.
    # A bare generator id with no plugin_module must fail closed and point at
    # the plugin mechanism.
    with pytest.raises(ValueError, match='builtin:clean_port'):
        get_generator("clean_port_ind_sym")


def test_missing_plugin_file_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="plugin module not found"):
        load_plugin_generators(tmp_path / "nope.py")


def test_plugin_without_export_fails_closed(tmp_path):
    bad = tmp_path / "noexport.py"
    bad.write_text("X = 1\n")
    with pytest.raises(ValueError, match="PLUGIN_GENERATORS"):
        load_plugin_generators(bad)


def test_plugin_with_wrong_value_type_fails_closed(tmp_path):
    bad = tmp_path / "wrongtype.py"
    bad.write_text("PLUGIN_GENERATORS = {'x': object()}\n")
    with pytest.raises(ValueError, match="PassiveDeviceGenerator"):
        load_plugin_generators(bad)


def test_plugin_id_mismatch_fails_closed(tmp_path):
    bad = tmp_path / "mismatch.py"
    bad.write_text(textwrap.dedent("""
        from ic_opt.em.pcell.base import (
            GeometryGenerationResult, PassiveDeviceGenerator)
        from pydantic import BaseModel

        class _Cfg(BaseModel):
            pass

        class _Gen(PassiveDeviceGenerator):
            generator_id = "real_id"
            config_model = _Cfg
            def generate(self, config, *, outdir, gds_name):
                raise NotImplementedError

        PLUGIN_GENERATORS = {"other_id": _Gen()}
    """))
    with pytest.raises(ValueError, match="does not match"):
        load_plugin_generators(bad)


def test_plugin_id_colliding_with_builtin_fails_closed(tmp_path, monkeypatch):
    # M12: there are no real built-in ids left to collide with (_BUILTIN_IDS
    # is now empty), but the collision-guard mechanism itself is generic
    # infrastructure kept for any future built-in. Exercise it directly by
    # injecting a fake built-in id.
    from ic_opt.em.pcell import registry

    monkeypatch.setattr(registry, "_BUILTIN_IDS", ("fake_builtin_id",))
    bad = tmp_path / "collide.py"
    bad.write_text(textwrap.dedent("""
        from ic_opt.em.pcell.base import (
            GeometryGenerationResult, PassiveDeviceGenerator)
        from pydantic import BaseModel

        class _Cfg(BaseModel):
            pass

        class _Gen(PassiveDeviceGenerator):
            generator_id = "fake_builtin_id"
            config_model = _Cfg
            def generate(self, config, *, outdir, gds_name):
                raise NotImplementedError

        PLUGIN_GENERATORS = {"fake_builtin_id": _Gen()}
    """))
    with pytest.raises(ValueError, match="collides with a built-in"):
        load_plugin_generators(bad)


def test_unknown_id_error_lists_available(tmp_path):
    with pytest.raises(ValueError, match="clean_port_ind_sym"):
        get_generator("no_such_generator", plugin_module=REAL_PLUGIN)


# -- builtin: plugin_module resolution (M11 R2) -------------------------


def test_load_plugin_generators_resolves_builtin_clean_port():
    generators = load_plugin_generators("builtin:clean_port")
    assert set(generators) == {"clean_port_ind_sym", "clean_port_xfm_bs",
                               "clean_port_xfm_ms", "clean_port_xfm_balun",
                               "clean_port_xfm_tw", "clean_port_xfm_il"}


def test_get_generator_resolves_builtin_clean_port():
    gen = get_generator("clean_port_ind_sym", plugin_module="builtin:clean_port")
    assert gen.generator_id == "clean_port_ind_sym"


def test_load_plugin_generators_unknown_builtin_fails_closed():
    with pytest.raises(ValueError, match="unknown builtin plugin module.*clean_port"):
        load_plugin_generators("builtin:no_such_builtin")


def test_load_plugin_generators_builtin_and_absolute_path_agree():
    """Same underlying file either way it's addressed."""
    via_builtin = load_plugin_generators("builtin:clean_port")
    from ic_opt.em.pcell import generator_plugin as gp_mod
    via_path = load_plugin_generators(Path(gp_mod.__file__))
    assert set(via_builtin) == set(via_path)


# -- plugin_module path rules ------------------------------------------------


def test_load_plugin_generators_rejects_relative_path():
    with pytest.raises(ValueError, match="absolute"):
        load_plugin_generators("relative/x.py")


def test_load_plugin_generators_is_thread_safe(tmp_path):
    import concurrent.futures
    import textwrap

    slow = tmp_path / "slow_plugin.py"
    slow.write_text(textwrap.dedent("""
        import time
        time.sleep(1.0)  # widen the exec window so the race is deterministic

        from ic_opt.em.pcell.base import PassiveDeviceGenerator
        from pydantic import BaseModel

        class _Cfg(BaseModel):
            pass

        class _Gen(PassiveDeviceGenerator):
            generator_id = "slow_gen"
            config_model = _Cfg
            def generate(self, config, *, outdir, gds_name):
                raise NotImplementedError

        PLUGIN_GENERATORS = {"slow_gen": _Gen()}
    """))

    def probe():
        return set(load_plugin_generators(slow))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: probe(), range(8)))
    assert all(r == {"slow_gen"} for r in results), results
