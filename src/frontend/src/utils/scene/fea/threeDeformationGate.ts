// The deformation gate (./deformationGate.ts) on the three.js viewport: the influence goes to the
// morph of the streaming loader's result mesh and of its beam sections.

import type * as THREE from "three";

import {applyFeaDeformationGate} from "./deformationGate";
import {feaSession} from "./streaming/session";

function setInfluence(mesh: THREE.Mesh | null | undefined, influence: number): void {
    const inf = mesh?.morphTargetInfluences;
    if (inf && inf.length > 0) inf[0] = influence;
}

function writeToMeshes(influence: number): void {
    const active = feaSession.active;
    if (!active) return;
    setInfluence(active.mesh, influence);
    setInfluence(active.beamSolidMesh, influence);
}

/** Called by the three.js render loop each frame, before drawing. */
export function applyThreeFeaDeformationGate(): void {
    applyFeaDeformationGate(writeToMeshes);
}
