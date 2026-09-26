"""A contract-conformant Abaqus/CAE concept-model script, used to prove the checker has teeth.

This is **not** an oracle for the writer and it is not a stub of the CAE kernel. It is a specimen of
the artefact under test: a script shaped the way `src/ada/cadit/cae/` is contracted to emit one. The
graph checker is asserted to accept it and to reject each single defect injected into it, so a green
run of the checker against the real writer means something.

Its authority comes from the kernel, not from its author: `test_cae_licensed_acceptance.py` runs this
very file through `abq2025 cae noGUI=` whenever a licence is present, so every CAE call below is known
to be accepted by Abaqus 2025 rather than believed to be.

Model: a portal frame whose brace lands mid-span of the girder, which splits the girder into two
sub-edges (probed: 1 edge -> 3 edges over the part). Members are therefore located by bounding
cylinder, never by a midpoint. The orientations are adapy's `beam.yvec` for these members. It also
carries an analysis -- a static step, two supports (one of them a prescribed settlement) and a
force and a moment -- on the *geometry*, with no mesh, because that is how a CAE support and load
attach and because the graph checker has a pass that reads them.

`from abaqus import *` is the CAE journal preamble and is mandatory here, so the star-import warnings
are switched off for the whole file rather than line by line -- a per-line `noqa` moves when the
formatter rewraps a call, and the lines in this file are mutation anchors.
"""

# ruff: noqa: F403, F405

from __future__ import print_function

import json
import sys

from abaqus import *
from abaqusConstants import *
from caeModules import *

RESULT_PATH = "specimen_frame_cae.cae_build_result.json"
RESULT = {"created": [], "skipped": [], "error": None}

# Units: metres, newtons. The model extent below is ~6 m, which is structural scale.
MODEL_NAME = "Model-1"

try:
    m = mdb.models[MODEL_NAME]
    m.setValues(description="adapy concept model, units: m / N")

    p = m.Part(name="Frame", dimensionality=THREE_D, type=DEFORMABLE_BODY)
    RESULT["created"].append("part Frame")

    p.WirePolyLine(points=(((0.0, 0.0, 0.0), (0.0, 0.0, 4.0)),), mergeType=IMPRINT, meshable=ON)
    p.WirePolyLine(points=(((0.0, 0.0, 4.0), (6.0, 0.0, 4.0)),), mergeType=IMPRINT, meshable=ON)
    p.WirePolyLine(points=(((3.0, 0.0, 4.0), (3.0, 2.0, 0.0)),), mergeType=IMPRINT, meshable=ON)
    RESULT["created"].append("wires 3")

    m.Material(name="S355")
    m.materials["S355"].Elastic(table=((2.1e11, 0.3),))
    m.materials["S355"].Density(table=((7850.0,),))
    RESULT["created"].append("material S355")

    m.IProfile(name="IPE300", l=0.15, h=0.3, b1=0.15, b2=0.15, t1=0.0107, t2=0.0107, t3=0.0071)
    m.BoxProfile(name="BG200", a=0.2, b=0.2, uniformThickness=ON, t1=0.01)
    m.LProfile(name="HP200", a=0.2, b=0.2, t1=0.01, t2=0.01)
    RESULT["created"].append("profiles 3")

    m.BeamSection(name="sec_IPE300", profile="IPE300", material="S355", integration=DURING_ANALYSIS)
    m.BeamSection(name="sec_BG200", profile="BG200", material="S355", integration=DURING_ANALYSIS)
    m.BeamSection(name="sec_HP200", profile="HP200", material="S355", integration=DURING_ANALYSIS)
    RESULT["created"].append("sections 3")

    edges_col1 = p.edges.getByBoundingCylinder(center1=(0.0, 0.0, -0.01), center2=(0.0, 0.0, 4.01), radius=0.001)
    if len(edges_col1) == 0:
        raise ValueError("no edge found for member col1")
    region_col1 = p.Set(name="col1", edges=edges_col1)
    p.SectionAssignment(region=region_col1, sectionName="sec_IPE300")
    p.assignBeamSectionOrientation(region=region_col1, method=N1_COSINES, n1=(1.0, 0.0, 0.0))
    RESULT["created"].append("member col1")

    edges_girder = p.edges.getByBoundingCylinder(center1=(-0.01, 0.0, 4.0), center2=(6.01, 0.0, 4.0), radius=0.001)
    if len(edges_girder) == 0:
        raise ValueError("no edge found for member girder")
    region_girder = p.Set(name="girder", edges=edges_girder)
    p.SectionAssignment(region=region_girder, sectionName="sec_BG200")
    p.assignBeamSectionOrientation(region=region_girder, method=N1_COSINES, n1=(0.0, 1.0, 0.0))
    RESULT["created"].append("member girder")

    edges_brace = p.edges.getByBoundingCylinder(
        center1=(3.0, -0.004472135955, 4.008944271910),
        center2=(3.0, 2.004472135955, -0.008944271910),
        radius=0.001,
    )
    if len(edges_brace) == 0:
        raise ValueError("no edge found for member brace")
    region_brace = p.Set(name="brace", edges=edges_brace)
    p.SectionAssignment(region=region_brace, sectionName="sec_HP200")
    p.assignBeamSectionOrientation(region=region_brace, method=N1_COSINES, n1=(-1.0, 0.0, 0.0))
    RESULT["created"].append("member brace")

    a = m.rootAssembly
    a.DatumCsysByDefault(CARTESIAN)
    a.Instance(name="Frame-1", part=p, dependent=ON)
    RESULT["created"].append("instance Frame-1")

    # --- the analysis, on the geometry and with no mesh: a CAE support, load and step all attach to
    # vertices, so meshing is a separate choice. The region helper is here rather than inlined for
    # the reason the writer's is: findAt returns an EMPTY sequence for a point that is not on a
    # vertex, so a region that does not exist has to raise rather than quietly become a support
    # attached to nothing.
    def _analysis_region(assembly, set_name, instance_name, points, source_set_name):
        instance = assembly.instances[instance_name]
        found = None
        for point in points:
            hit = instance.vertices.findAt((point,))
            if len(hit) == 0:
                raise ValueError("region {0} (from {1}): no vertex at {2}".format(set_name, source_set_name, point))
            found = hit if found is None else found + hit
        assembly.Set(name=set_name, vertices=found)

    _analysis_region(a, "BASE", "Frame-1", ((0.0, 0.0, 0.0),), "BASE")
    _analysis_region(a, "BRACE_FOOT", "Frame-1", ((3.0, 2.0, 0.0),), "BRACE_FOOT")
    _analysis_region(a, "TOP", "Frame-1", ((6.0, 0.0, 4.0),), "TOP")
    RESULT["created"].append("regions 3")

    m.StaticStep(
        name="lc1",
        previous="Initial",
        timePeriod=1.0,
        initialInc=1.0,
        minInc=1e-08,
        maxInc=1.0,
        maxNumInc=100,
        nlgeom=OFF,
    )
    m.FieldOutputRequest(name="F-adapy", createStepName="lc1", variables=("U", "UR", "RF", "RM", "SF", "S", "E"))
    # One fully fixed support and one carrying a prescribed settlement, with the free DOFs written
    # UNSET rather than left out so a reader never has to know what CAE's default is.
    #
    # The settlement is declared in "lc1" and NOT in "Initial", and that is measured rather than a
    # preference: Abaqus refuses a non-zero boundary condition in the initial step outright --
    # "BUILD FAILED Non-zero boundary condition in initial step." -- which is why the writer sends
    # a prescribed support to the first analysis step and a fixed one to "Initial".
    m.DisplacementBC(
        name="fix_base",
        createStepName="Initial",
        region=a.sets["BASE"],
        u1=0.0,
        u2=0.0,
        u3=0.0,
        ur1=0.0,
        ur2=0.0,
        ur3=0.0,
    )
    m.DisplacementBC(
        name="settle_brace",
        createStepName="lc1",
        region=a.sets["BRACE_FOOT"],
        u1=0.0,
        u2=0.0,
        u3=-0.002,
        ur1=UNSET,
        ur2=UNSET,
        ur3=UNSET,
    )
    m.ConcentratedForce(name="px_F", createStepName="lc1", region=a.sets["TOP"], cf1=5000.0, cf2=0.0, cf3=0.0)
    m.Moment(name="px_M", createStepName="lc1", region=a.sets["TOP"], cm1=0.0, cm2=1000.0, cm3=0.0)
    RESULT["created"].append("analysis lc1")
    print("analysis", len(m.boundaryConditions), "bcs", len(m.loads), "loads")

    # The one check that catches both connectivity failure modes: an unassigned sub-edge left behind
    # by imprinting, and a wire that never joined the rest of the part.
    covered = set()
    for assignment in p.sectionAssignments:
        for edge in p.sets[assignment.region[0]].edges:
            covered.add(edge.index)
    if len(covered) != len(p.edges):
        raise ValueError("{0} of {1} edges have a section assignment".format(len(covered), len(p.edges)))
    print("edges", len(p.edges), "all sectioned")
    RESULT["created"].append("edge coverage {0}/{1}".format(len(covered), len(p.edges)))
except Exception as exc:  # noqa: BLE001
    RESULT["error"] = str(exc)
    with open(RESULT_PATH, "w") as fh:
        json.dump(RESULT, fh, indent=2, sort_keys=True)
    print("BUILD FAILED", str(exc))
    sys.exit(1)

with open(RESULT_PATH, "w") as fh:
    json.dump(RESULT, fh, indent=2, sort_keys=True)
print("BUILD OK")
