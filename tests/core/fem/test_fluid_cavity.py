"""Gas-filled cavities: the enclosed volume and ideal-gas helpers, the FEM container, and the writers."""

from __future__ import annotations

import math

import numpy as np
import pytest

import ada
from ada.fem import CavityVent, Elem, FemSet, FluidCavity, IdealGas, Surface
from ada.fem.cavities import (
    UNIVERSAL_GAS_CONSTANT,
    check_closed,
    enclosed_volume,
    signed_volume,
)
from ada.fem.exceptions import UnsupportedFeature

from .theory_models import closed_box

AIR = 0.028964


def _gas(**kw):
    base = dict(
        molecular_weight=AIR, initial_pressure=120_000.0, initial_temperature=293.15, ambient_pressure=101_325.0
    )
    base.update(kw)
    return IdealGas(**base)


# ---- the ideal gas ---------------------------------------------------------------------------------------------


def test_the_ideal_gas_law():
    gas = _gas()
    assert gas.initial_density == pytest.approx(120_000.0 * AIR / (UNIVERSAL_GAS_CONSTANT * 293.15))
    assert gas.mass(2.0) == pytest.approx(2.0 * gas.initial_density)
    assert gas.initial_gauge_pressure == pytest.approx(120_000.0 - 101_325.0)
    # Isothermal, half the volume: twice the pressure; and Gay-Lussac at constant volume.
    assert gas.pressure(1.0, 2.0) == pytest.approx(240_000.0)
    assert gas.pressure(2.0, 2.0, temperature=2 * 293.15) == pytest.approx(240_000.0)
    # p V = m R T / M holds at the new state.
    v, t = 1.3, 310.0
    assert gas.pressure(v, 2.0, temperature=t) * v == pytest.approx(gas.mass(2.0) * UNIVERSAL_GAS_CONSTANT * t / AIR)


def test_an_adiabatic_gas():
    gas = _gas(adiabatic=True, gamma=1.4)
    assert gas.pressure(1.0, 2.0) == pytest.approx(120_000.0 * 2.0**1.4)
    assert gas.temperature(1.0, 2.0) == pytest.approx(293.15 * 2.0**0.4)
    assert gas.molar_cp == pytest.approx(3.5 * UNIVERSAL_GAS_CONSTANT)
    with pytest.raises(ValueError, match="follows from its volume"):
        gas.pressure(1.0, 2.0, temperature=300.0)


@pytest.mark.parametrize(
    "kw", [dict(initial_pressure=0.0), dict(initial_temperature=-1.0), dict(gamma=1.0), dict(molecular_weight=0.0)]
)
def test_an_impossible_gas_is_refused(kw):
    with pytest.raises(ValueError):
        _gas(**kw)


# ---- the enclosed volume ---------------------------------------------------------------------------------------


def test_a_box_of_shells_encloses_its_volume():
    _, _, fs = closed_box(2.0, 3.0, 4.0)
    assert enclosed_volume(fs) == pytest.approx(24.0)
    # Outward normals give a positive signed volume, the negative faces a negative one.
    from ada.fem.cavities import oriented_facets

    assert signed_volume(oriented_facets(fs)) == pytest.approx(24.0)
    neg = Surface("neg", "ELEMENT", fs, el_face_index=-1)
    assert signed_volume(oriented_facets(neg)) == pytest.approx(-24.0)
    assert enclosed_volume(neg) == pytest.approx(24.0)


def _solid(points, el_type):
    p = ada.Part("s")
    nodes = [p.fem.nodes.add(ada.Node(q, i + 1)) for i, q in enumerate(points)]
    el = p.fem.add_elem(Elem(1, nodes, el_type))
    fs = p.fem.add_set(FemSet("solid", [el], "elset"))
    return Surface("free", "ELEMENT", fs, el_face_index="")


def test_the_free_faces_of_solids_enclose_their_volume():
    hex8 = [(0, 0, 0), (2, 0, 0), (2, 3, 0), (0, 3, 0), (0, 0, 4), (2, 0, 4), (2, 3, 4), (0, 3, 4)]
    assert enclosed_volume(_solid(hex8, "HEXAHEDRON")) == pytest.approx(24.0)
    tet = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    assert enclosed_volume(_solid(tet, "TETRA")) == pytest.approx(1 / 6)


def _sphere(r: float, refine: int):
    """An icosphere of radius ``r`` as 3-node shells, normals outward."""
    t = (1 + 5**0.5) / 2
    verts = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t)]
    verts += [(t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    verts = [np.array(v) / np.linalg.norm(v) for v in verts]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
        (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
        (8, 6, 7), (9, 8, 1),
    ]  # fmt: skip
    for _ in range(refine):
        mid, new = {}, []

        def midpoint(i, j):
            key = (min(i, j), max(i, j))
            if key not in mid:
                m = verts[i] + verts[j]
                verts.append(m / np.linalg.norm(m))
                mid[key] = len(verts) - 1
            return mid[key]

        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = new
    p = ada.Part("sphere")
    nodes = [p.fem.nodes.add(ada.Node(r * v, i + 1)) for i, v in enumerate(verts)]
    els = [p.fem.add_elem(Elem(i + 1, [nodes[k] for k in f], "TRIANGLE")) for i, f in enumerate(faces)]
    return p, p.fem.add_set(FemSet("sphere", els, "elset")), faces, [r * v for v in verts]


def test_a_sphere_of_triangles_encloses_the_polyhedron_and_tends_to_the_sphere():
    r = 2.0
    previous = 0.0
    for refine in range(4):
        _, fs, faces, verts = _sphere(r, refine)
        # The polyhedron's volume, independently: tetrahedra from the centre, by determinant.
        exact = sum(abs(np.linalg.det(np.array([verts[a], verts[b], verts[c]]))) / 6 for a, b, c in faces)
        volume = enclosed_volume(fs)
        assert volume == pytest.approx(exact, rel=1e-12)
        assert previous < volume < 4 / 3 * math.pi * r**3
        previous = volume
    assert previous == pytest.approx(4 / 3 * math.pi * r**3, rel=0.01)


def test_an_open_or_inconsistent_surface_is_not_a_cavity():
    _, p, fs = closed_box()
    open_set = FemSet("open", list(fs.members)[1:], "elset", parent=p.fem)
    with pytest.raises(ValueError, match="not closed"):
        check_closed(open_set)
    first = list(fs.members)[0]
    flipped = p.fem.add_elem(Elem(99, list(first.nodes)[::-1], "QUAD"))
    inconsistent = FemSet("flipped", [flipped] + list(fs.members)[1:], "elset", parent=p.fem)
    with pytest.raises(ValueError, match="not oriented consistently"):
        enclosed_volume(inconsistent)


# ---- the FEM container -----------------------------------------------------------------------------------------


def test_a_cavity_is_registered_with_its_surface_and_reference_node():
    a, p, fs = closed_box()
    ref = ada.Node((1.0, 1.5, 2.0))
    surf = Surface("cushion", "ELEMENT", fs, el_face_index=1)
    cav = p.fem.add_fluid_cavity(FluidCavity("cav", surf, ref, _gas()))
    assert p.fem.fluid_cavities["cav"] is cav and cav.parent is p.fem
    assert p.fem.surfaces["cushion"] is surf
    assert ref.parent is p.fem and p.fem.nodes.from_id(ref.id) is ref
    assert cav.volume() == pytest.approx(24.0)
    assert [el.id for el in cav.shell_elements()] == [1, 2, 3, 4, 5, 6]
    with pytest.raises(ValueError, match="already exists"):
        p.fem.add_fluid_cavity(FluidCavity("cav", fs, ada.Node((0, 0, 0)), _gas()))


def test_a_node_set_bounds_no_cavity():
    _, p, fs = closed_box()
    nset = p.fem.add_set(FemSet("pts", [p.fem.nodes.from_id(1)], "nset"))
    with pytest.raises(ValueError, match="node set"):
        FluidCavity("c", nset, ada.Node((0, 0, 0)), _gas())


def test_merging_fems_carries_the_cavities():
    _, p, fs = closed_box()
    p.fem.add_fluid_cavity(FluidCavity("cav", fs, ada.Node((1, 1, 1)), _gas()))
    other = ada.Part("other")
    other.fem += p.fem
    assert "cav" in other.fem.fluid_cavities and other.fem.fluid_cavities["cav"].parent is other.fem


# ---- writers ---------------------------------------------------------------------------------------------------


def _box_with_cavity(adiabatic=False, theory="shell"):
    a, p, fs = closed_box(theory=theory)
    ref = ada.Node((1.0, 1.5, 2.0))
    p.fem.add_fluid_cavity(FluidCavity("cav", fs, ref, _gas(adiabatic=adiabatic)))
    return a, p, ref


def test_abaqus_writes_a_fluid_cavity(tmp_path):
    a, p, ref = _box_with_cavity(adiabatic=True)
    a.to_fem("cav", "abaqus", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "cav" / "cav.inp").read_text()
    instance = p.fem.instance_name
    assert "*Surface, type=ELEMENT, name=cav_surf\nbox, SPOS" in deck
    assert (
        f"*Fluid Cavity, ADIABATIC, name=cav, behavior=cav_gas, ref node={ref.id}, surface=cav_surf, "
        "ambient=101325.0" in deck
    )
    assert "*Physical Constants, absolute zero=0.0, universal gas constant=8.314462618" in deck
    assert "*Fluid Behavior, name=cav_gas\n*Molecular Weight\n 0.028964," in deck
    cp = 3.5 * UNIVERSAL_GAS_CONSTANT
    capacity = deck.split("*Capacity, type=POLYNOMIAL\n")[1].splitlines()[0]
    assert float(capacity.split(",")[0]) == pytest.approx(cp)
    assert f"*Initial Conditions, type=FLUID PRESSURE\n{instance}.{ref.id}, 18675.0" in deck
    assert f"*Initial Conditions, type=TEMPERATURE\n{instance}.{ref.id}, 293.15" in deck


def test_abaqus_writes_an_isothermal_cavity_without_the_adiabatic_flag(tmp_path):
    a, _, _ = _box_with_cavity(adiabatic=False)
    a.to_fem("cav", "abaqus", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "cav" / "cav.inp").read_text()
    assert "*Fluid Cavity, name=cav," in deck and "ADIABATIC" not in deck


def test_opencourant_writes_a_monvol_gas(tmp_path):
    a, p, _ = _box_with_cavity(adiabatic=True)
    a.to_fem("cav", "opencourant", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "cav" / "cav_0000.rad").read_text()
    # a monitored volume needs a surface made of shells: a group of the box's quads, not a segment list
    group = deck.split("/GRSHEL/SHEL/1\n")[1].splitlines()
    assert group[0] == "cav_surf_shel"
    assert [int(group[1][i : i + 10]) for i in range(0, 60, 10)] == [1, 2, 3, 4, 5, 6]
    assert "/SURF/GRSHEL/1\ncav_surf_shel\n         1" in deck
    assert "/SURF/SEG/" not in deck
    rows = [r for r in deck.split("/MONVOL/GAS/1\n")[1].splitlines() if not r.startswith("#")]
    assert rows[0] == "cav"
    assert [int(rows[1][i : i + 10]) for i in (0, 10)] == [1, 0]  # surf_IDex, I_equi
    f20 = lambda row: [float(row[i : i + 20]) for i in range(0, len(row), 20)]  # noqa: E731
    gas = _gas(adiabatic=True)
    rho_ext = 101_325.0 * AIR / (UNIVERSAL_GAS_CONSTANT * 293.15)
    assert f20(rows[3]) == pytest.approx([1.4, 0.0, 0.0, 293.15, rho_ext])
    # Mini is the gas's mass: the vents' outflow density is Mini / V
    mass = gas.initial_density * 24.0
    assert f20(rows[4]) == pytest.approx([101_325.0, gas.initial_pressure, 0.0, 0.0, mass])
    assert int(rows[5]) == 0  # no vents
    th = deck.split("/TH/MONV/1\n")[1].splitlines()
    assert th[2].split() == ["MASS", "VOL", "P", "A", "T", "AO", "UO"]
    assert int(th[4]) == 1


def test_opencourant_writes_a_cavity_vent(tmp_path):
    a, p, fs = closed_box(theory="shell")
    vent = CavityVent(area=0.05, open_time=0.2)
    p.fem.add_fluid_cavity(FluidCavity("cav", fs, ada.Node((1.0, 1.5, 2.0)), _gas(adiabatic=True), vents=[vent]))
    a.to_fem("cav", "opencourant", scratch_dir=tmp_path, overwrite=True)
    deck = (tmp_path / "cav" / "cav_0000.rad").read_text()
    rows = [r for r in deck.split("/MONVOL/GAS/1\n")[1].split("/TH/")[0].splitlines() if not r.startswith("#")]
    assert int(rows[5]) == 1
    assert int(rows[6][:10]) == 0 and float(rows[6][10:30]) == pytest.approx(0.05)  # area, no vent surface
    # opens at its time only, never on an over-pressure
    assert [float(rows[7][i : i + 20]) for i in (0, 20)] == pytest.approx([0.2, 1.0e30])


def test_a_vent_needs_an_area_and_a_time_not_before_the_start(tmp_path):
    with pytest.raises(ValueError):
        CavityVent(area=0.0)
    with pytest.raises(ValueError):
        CavityVent(area=0.1, open_time=-1.0)
    a, p, fs = closed_box()
    p.fem.add_fluid_cavity(FluidCavity("vented", fs, ada.Node((0, 0, 0)), _gas(), vents=[CavityVent(0.1)]))
    with pytest.raises(NotImplementedError):  # the Abaqus writer has no fluid exchange for a vent yet
        a.to_fem("cav", "abaqus", scratch_dir=tmp_path, overwrite=True)
    with pytest.raises(TypeError):
        FluidCavity("cav", fs, ada.Node((0, 0, 0)), _gas(), vents=[0.1])


@pytest.mark.parametrize("fem_format", ["calculix", "code_aster", "sesam", "usfos"])
def test_writers_without_a_gas_cavity_refuse_it(fem_format, tmp_path):
    a, _, _ = _box_with_cavity()
    with pytest.raises(UnsupportedFeature, match="no gas-filled cavity"):
        a.to_fem("cav", fem_format, scratch_dir=tmp_path, overwrite=True)
