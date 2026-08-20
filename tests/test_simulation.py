from cadfree.matlab.engine import _command, find_engine
from cadfree.simulation.pipeline import octave_beam_script, probe
from cadfree.manufacturing.types import MeshMetrics


def test_octave_command_shape():
    cmd = _command("/usr/bin/octave", "/tmp/x.m", "/tmp")
    assert cmd[0].endswith("octave")
    assert "--no-gui" in cmd


def test_matlab_command_shape():
    cmd = _command("/usr/local/bin/matlab", "/tmp/x.m", "/tmp")
    assert cmd[1] == "-batch"


def test_engine_probe_does_not_throw():
    status = find_engine()
    assert "available" in status
    if not status["available"]:
        assert "install_hint" in status


def test_beam_script_contains_si_units():
    metrics = MeshMetrics(
        volume_mm3=8000,
        surface_area_mm2=2000,
        bbox_mm=(80, 40, 6),
        watertight=True,
        triangle_count=12,
        solidity=0.4,
    )
    script = octave_beam_script(metrics, {"flex_modulus_gpa": 2.1, "tensile_xy_mpa": 40}, {"load_lbf": 50})
    assert "sigma =" in script
    assert probe()["first_order"]["available"] is True
