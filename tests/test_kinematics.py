import math

import numpy as np

from cadfree.kinematics.collision import aabb_overlap, convex_overlap, gear_center_check
from cadfree.kinematics.fourbar import link_stats, solve_fourbar, transmission_angle_deg
from cadfree.kinematics.geometry import circle_circle
from cadfree.kinematics.slidercrank import solve_slider_crank


def test_circle_circle_two_hits():
    hits = circle_circle(np.array([0.0, 0.0]), 5.0, np.array([6.0, 0.0]), 5.0)
    assert len(hits) == 2
    assert abs(hits[0][0] - 3.0) < 1e-6


def test_fourbar_assembles_and_grashof():
    stats = link_stats([30, 70, 55, 80])
    assert stats["grashof"] is True
    assert stats["class"] == "crank-rocker"
    solved = solve_fourbar(np.array([0.0, 0.0]), np.array([80.0, 0.0]), 30, 70, 55, 0.0)
    assert solved["ok"] is True
    assert solved["locked"] is False
    b = solved["B"]
    assert abs(b[0] - 30) < 1e-6
    assert solved["transmission_deg"] > 20


def test_transmission_angle_right_angle():
    mu = transmission_angle_deg(np.array([0.0, 0.0]), np.array([1.0, 0.0]), np.array([1.0, 1.0]))
    assert abs(mu - 90.0) < 1e-6


def test_fourbar_lockup():
    solved = solve_fourbar(np.array([0.0, 0.0]), np.array([200.0, 0.0]), 20, 20, 20, 0.0)
    assert solved["ok"] is False
    assert solved["locked"] is True


def test_full_crank_rotation_grashof():
    prev = None
    locked = 0
    for i in range(36):
        th = i * math.tau / 36
        out = solve_fourbar(np.array([0.0, 0.0]), np.array([80.0, 0.0]), 30, 70, 55, th, prev)
        if not out["ok"]:
            locked += 1
        else:
            prev = np.array(out["C"])
    assert locked == 0


def test_gear_pitch_meshes():
    ok = gear_center_check(60.0, 20, 40, 2.0)
    assert ok["ok"] is True
    far = gear_center_check(90.0, 20, 40, 2.0)
    assert far["ok"] is False
    tight = gear_center_check(50.0, 20, 40, 2.0)
    assert tight["ok"] is False


def test_aabb_overlap():
    a = np.array([[0, 0, 0], [1, 1, 1]], dtype=float)
    b = np.array([[0.5, 0.5, 0.5], [2, 2, 2]], dtype=float)
    c = np.array([[10, 10, 10], [11, 11, 11]], dtype=float)
    assert aabb_overlap(a, b) is True
    assert aabb_overlap(a, c) is False


def _cube(origin):
    o = np.asarray(origin, dtype=float)
    v = o + np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        dtype=float,
    )
    f = np.array(
        [
            [0, 1, 2], [0, 2, 3],
            [4, 6, 5], [4, 7, 6],
            [0, 4, 5], [0, 5, 1],
            [3, 2, 6], [3, 6, 7],
            [0, 3, 7], [0, 7, 4],
            [1, 5, 6], [1, 6, 2],
        ],
        dtype=int,
    )
    return v, f


def test_convex_sat_not_just_aabb():
    va, fa = _cube([0, 0, 0])
    vb, fb = _cube([0.4, 0.4, 0.4])
    assert convex_overlap(va, vb, fa, fb) is True
    vc, fc = _cube([3.0, 0.0, 0.0])
    assert convex_overlap(va, vc, fa, fc) is False


def test_slider_crank_assembles():
    out = solve_slider_crank(
        np.array([0.0, 0.0]), np.array([0.0, 0.0]), np.array([1.0, 0.0]), 30.0, 70.0, 0.0
    )
    assert out["ok"] is True
    assert out["locked"] is False
    assert abs(abs(out["C"][0] - 30.0) - 70.0) < 1e-6
    miss = solve_slider_crank(
        np.array([0.0, 0.0]), np.array([0.0, 200.0]), np.array([1.0, 0.0]), 10.0, 10.0, 0.0
    )
    assert miss["locked"] is True


def test_fourbar_api_sweep(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from fastapi.testclient import TestClient

    from cadfree.cad.assembly import list_parts, place_instance
    from cadfree.kinematics.fourbar import solve_fourbar
    from cadfree.main import create_app

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "fourbar", "spec_text": "linkage"}).json()["id"]
    project = client.get(f"/api/projects/{pid}").json()
    crank = project["assembly"]["instances"][0]["id"]
    part_id = list_parts(pid)[0]["id"]
    coupler = place_instance(pid, part_id, name="coupler", loc={"x": 30, "y": 0, "z": 0})["id"]
    rocker = place_instance(pid, part_id, name="rocker", loc={"x": 80, "y": 0, "z": 0})["id"]
    rest = solve_fourbar(np.array([0.0, 0.0]), np.array([80.0, 0.0]), 30, 70, 55, 0.0)
    c = rest["C"]
    pins = [
        ("ground-crank", "", crank, {"x": 0, "y": 0, "z": 0}, True),
        ("crank-coupler", crank, coupler, {"x": 30, "y": 0, "z": 0}, False),
        ("coupler-rocker", coupler, rocker, {"x": c[0], "y": c[1], "z": 0}, False),
        ("rocker-ground", "", rocker, {"x": 80, "y": 0, "z": 0}, False),
    ]
    for name, a, b, origin, driven in pins:
        posted = client.post(
            f"/api/projects/{pid}/joints",
            json={
                "name": name,
                "kind": "revolute",
                "instance_a": a,
                "instance_b": b,
                "driven": driven,
                "axis": "z",
                "origin": origin,
            },
        )
        assert posted.status_code == 200, posted.text
    motion = client.get(f"/api/projects/{pid}/motion", params={"end_deg": 360, "steps": 12})
    assert motion.status_code == 200
    body = motion.json()
    assert body["ok"] is True
    assert body["kind"] == "fourbar"
    assert body["stats"]["grashof"] is True
    assert body["stats"]["class"] == "crank-rocker"
    assert not body["lockups"]
    assert body.get("transmission_min_deg") is not None
    check = client.get(f"/api/projects/{pid}/mechanism").json()
    assert check["verdict"] in {"works", "awkward", "collides"}
    assert check.get("for_model")
    assert check.get("comfort")


def test_handlers_joint_and_mesh(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from fastapi.testclient import TestClient

    from cadfree.agent.tools import make_handlers
    from cadfree.cad.assembly import list_parts, place_instance
    from cadfree.main import create_app

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "gears"}).json()["id"]
    project = client.get(f"/api/projects/{pid}").json()
    a = project["assembly"]["instances"][0]["id"]
    part_id = list_parts(pid)[0]["id"]
    b = place_instance(pid, part_id, name="gear-b", loc={"x": 60, "y": 0, "z": 0})["id"]
    h = make_handlers(pid)
    joint = h["define_joint"](
        "mesh",
        kind="gear",
        instance_a=a,
        instance_b=b,
        params={"module_mm": 2, "teeth_a": 20, "teeth_b": 40},
    )
    assert joint["ok"] is True
    mesh = h["check_mesh"]()
    assert mesh["gears"][0]["ok"] is True
    assert "verdict" in mesh
    assert joint["ok"] is True
    h2 = make_handlers(pid)
    # move B out of mesh
    place_instance(pid, part_id, name="gear-b", loc={"x": 90, "y": 0, "z": 0}, instance_id=b)
    miss = h2["check_mesh"]()
    assert miss["gears"][0]["ok"] is False

    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from fastapi.testclient import TestClient

    from cadfree.main import create_app

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "linkage", "spec_text": "four bar"}).json()["id"]
    project = client.get(f"/api/projects/{pid}").json()
    inst = project["assembly"]["instances"][0]["id"]
    posted = client.post(
        f"/api/projects/{pid}/joints",
        json={
            "name": "crank",
            "kind": "revolute",
            "instance_b": inst,
            "driven": True,
            "axis": "z",
            "origin": {"x": 0, "y": 0, "z": 0},
        },
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["joint"]["driven"] is True
    motion = client.get(f"/api/projects/{pid}/motion", params={"end_deg": 90, "steps": 5})
    assert motion.status_code == 200
    body = motion.json()
    assert body["ok"] is True
    assert body["kind"] == "open-chain"
    assert len(body["frames"]) == 5
    assert "disclaimer" in body
