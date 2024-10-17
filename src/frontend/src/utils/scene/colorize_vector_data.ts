import * as THREE from 'three';
import {useColorStore} from "../../state/colorLegendStore";

type Vector = number[];
type Matrix = Vector[];

export function updateMeshMaterial(mesh: THREE.Mesh) {
    // Check if the material supports vertex colors
    // Replace the material with one that supports vertex colors
    mesh.material = new THREE.MeshBasicMaterial({vertexColors: true});
    // make sure the material is double sided so that we can see the back side of the mesh
    mesh.material.side = THREE.DoubleSide;
    console.log('replaced material')
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

    // Create a color attribute
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
}
