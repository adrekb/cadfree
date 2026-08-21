from cadfree.physics.openfoam import probe_openfoam, write_openfoam_case


def test_openfoam_probe_does_not_throw():
    probe = probe_openfoam()
    assert "available" in probe
    if not probe["available"]:
        assert "install_hint" in probe
        assert "simpleFoam" in probe["install_hint"] or "PATH" in probe["install_hint"]


def test_openfoam_template_writes_without_engine(tmp_path):
    stl = tmp_path / "part_si.stl"
    stl.write_text("solid x\nendsolid x\n", encoding="utf-8")
    dest = tmp_path / "openfoam"
    written = write_openfoam_case(
        dest,
        stl=stl,
        bbox_m=[0.08, 0.04, 0.01],
        v_ms=12.0,
        rho=1.225,
        nu=1.5e-5,
    )
    assert (dest / "system" / "controlDict").is_file()
    assert (dest / "system" / "snappyHexMeshDict").is_file()
    assert (dest / "Allrun").is_file()
    assert (dest / "0" / "U").is_file()
    assert (dest / "constant" / "triSurface" / "part.stl").is_file()
    assert "system/controlDict" in written["files"]
    text = (dest / "system" / "controlDict").read_text(encoding="utf-8")
    assert "forceCoeffs" in text
    assert "simpleFoam" in text
