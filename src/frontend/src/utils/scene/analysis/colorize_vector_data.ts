import * as THREE from 'three';
import {useColorStore} from "../../../state/colorLegendStore";
import {CustomBatchedMesh} from "../../mesh_select/CustomBatchedMesh";

type Vector = number[];
type Matrix = Vector[];

export function updateMeshMaterial(mesh: THREE.Mesh) {
    // For CustomBatchedMesh, do not replace its material array; enable vertex colors on slot 0.
    if (mesh instanceof CustomBatchedMesh) {
        const mat0 = (Array.isArray(mesh.material) ? (mesh.material as THREE.Material[])[0] : mesh.material) as any;
        if (mat0 && 'vertexColors' in mat0) {
            mat0.vertexColors = true;
            (mat0 as THREE.Material).side = THREE.DoubleSide;
            (mat0 as THREE.Material).needsUpdate = true;
        }
        return;
    }
    // Otherwise, ensure the material supports vertex colors by replacing it
    mesh.material = new THREE.MeshBasicMaterial({vertexColors: true, side: THREE.DoubleSide});
}


function magnitude(u: Vector): number {
    return Math.sqrt(u.reduce((acc, val) => acc + val * val, 0));
}

function magnitude1d(u: Vector): number {
    return u[0];
}

class DataColorizer {
    public static colorizeData(data: Vector[], func: (u: Vector) => number = magnitude): Vector[] {
        if (data[0].length === 1) {
            func = magnitude1d;
        }
        const palette = useColorStore.getState().colorPalette;

        const sortedData = data.map(d => func(d)).sort((a, b) => a - b);
        const minR = sortedData[0];
        const maxR = sortedData[sortedData.length - 1];

        const start = palette[0];
        const end = palette[1];

        const currP = (t: number): Vector => {
            return start.map((startVal, index) => startVal + (end[index] - startVal) * (t - minR) / (maxR - minR));
        };

        return data.map(d => currP(func(d)));
    }
}

function convertTo3xN(flatList: number[]): Matrix {
    const result: Matrix = [];
    for (let i = 0; i < flatList.length; i += 3) {
        result.push([flatList[i], flatList[i + 1], flatList[i + 2]]);
    }
    return result;
}

export function colorVerticesBasedOnDeformation(mesh: THREE.Mesh, morphTargetIndex: number) {
    const geometry = mesh.geometry;
    const positionAttribute = geometry.attributes.position;
    const morphAttribute = geometry.morphAttributes.position[morphTargetIndex];
    const morph_3xN = convertTo3xN(Array.from(morphAttribute.array));
    const coloredData = DataColorizer.colorizeData(morph_3xN);

    // Create or replace the color attribute
    const colors = new Float32Array(positionAttribute.count * 3);
    const colorAttribute = new THREE.BufferAttribute(colors, 3);
    geometry.setAttribute('color', colorAttribute);

    // Apply the colors from coloredData to each vertex
    for (let i = 0; i < positionAttribute.count; i++) {
        const colorData = coloredData[i];
        const color = new THREE.Color(colorData[0], colorData[1], colorData[2]);
        colorAttribute.setXYZ(i, color.r, color.g, color.b);
    }

    // Update the color attribute
    colorAttribute.needsUpdate = true;

    // Update the material
    updateMeshMaterial(mesh);

    // If this is a batched mesh, capture these as base colors for selection overlay
    if (mesh instanceof CustomBatchedMesh) {
        mesh.setBaseColorsFromCurrent();
        mesh.reapplySelectionHighlight();
    }
}
