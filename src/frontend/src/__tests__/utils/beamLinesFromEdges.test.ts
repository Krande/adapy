import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";

import {FEA_EDGE_LINES_NAME, installBeamLinesFromEdges} from "@/utils/scene/fea/beamLinesFromEdges";
import {hasResultLineSegments} from "@/utils/scene/fea/resultLineSegments";

// A beam bundle's mesh is node points only, so its elements were drawn by nothing
// but a one-pixel grey edge hairline. The embed now draws them as the fat,
// result-coloured lines the standalone viewer uses, built from the edges and the
// points' colour and morph.

/** Three nodes along X, two beam elements (0-1, 1-2), the far node displaced +1 in Y. */
function beamPoints(withEdges = true) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 2, 0, 0], 3));
    geometry.setAttribute("color", new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 0, 1, 0, 0], 3));
    geometry.morphAttributes.position = [new THREE.Float32BufferAttribute([0, 0, 0, 0, 0.5, 0, 0, 1, 0], 3)];
    geometry.morphTargetsRelative = true;
    const points = new THREE.Points(geometry, new THREE.PointsMaterial());
    points.morphTargetInfluences = [1];
    if (withEdges) {
        const lineGeom = new THREE.BufferGeometry();
        lineGeom.setAttribute("position", geometry.getAttribute("position"));
        lineGeom.setIndex([0, 1, 1, 2]);
        const edges = new THREE.LineSegments(lineGeom, new THREE.LineBasicMaterial());
        edges.name = FEA_EDGE_LINES_NAME;
        points.add(edges);
    }
    return points;
}

function drawnLine(points: THREE.Object3D): LineSegments2 {
    return points.children.find((c) => (c as any).isLineSegments2) as LineSegments2;
}

test("each edge becomes a coloured fat-line segment and the hairline is hidden", () => {
    const points = beamPoints();
    assert.equal(installBeamLinesFromEdges(points), true);
    assert.equal(hasResultLineSegments(points), true);
    assert.equal(points.getObjectByName(FEA_EDGE_LINES_NAME)!.visible, false);

    const line = drawnLine(points);
    const colours = Array.from((line.geometry.getAttribute("instanceColorStart") as any).data.array as Float32Array);
    // Segment 1-2: its ends take nodes 1 and 2's colours.
    assert.deepEqual(colours.slice(6, 12), [0, 1, 0, 1, 0, 0]);
});

test("the lines follow the morph influence the driver writes", () => {
    const points = beamPoints();
    installBeamLinesFromEdges(points);
    const line = drawnLine(points);
    const renderer = {getSize: (v: THREE.Vector2) => v.set(100, 100)} as unknown as THREE.WebGLRenderer;
    const at = (influence: number) => {
        points.morphTargetInfluences![0] = influence;
        // The fat line's hook takes just the renderer (resultLineSegments assigns a one-argument one).
        (line.onBeforeRender as unknown as (r: THREE.WebGLRenderer) => void)(renderer);
        const arr = (line.geometry.getAttribute("instanceStart") as THREE.InterleavedBufferAttribute).data.array;
        return arr[10]; // segment 1-2, end node 2, Y
    };
    assert.equal(at(1), 1);
    assert.equal(at(-0.5), -0.5);
    assert.equal(at(1), 1); // no drift on a repeat
});

test("nothing to build from: no edges child", () => {
    const points = beamPoints(false);
    assert.equal(installBeamLinesFromEdges(points), false);
    assert.equal(hasResultLineSegments(points), false);
});
