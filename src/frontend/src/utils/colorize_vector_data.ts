import * as THREE from 'three';
import {useColorStore} from "../state/colorLegendStore";

export function updateMeshMaterial(mesh: THREE.Mesh) {
    // Check if the material supports vertex colors
    // Replace the material with one that supports vertex colors
    mesh.material = new THREE.MeshBasicMaterial({vertexColors: true});
    // make sure the material is double sided so that we can see the back side of the mesh
    mesh.material.side = THREE.DoubleSide;
    console.log('replaced material')
}


export function colorVerticesBasedOnDeformation(mesh: THREE.Mesh, morphTargetIndex: number) {
    const minColor = new THREE.Color('blue');
    const maxColor = new THREE.Color('red');
    const minHSL = minColor.getHSL({h: 0, s: 0, l: 0});
    const maxHSL = maxColor.getHSL({h: 0, s: 0, l: 0});

    const geometry = mesh.geometry;
    const positionAttribute = geometry.attributes.position;
    const morphAttribute = geometry.morphAttributes.position[morphTargetIndex];

    console.log('morphAttribute', morphAttribute)
    console.log('morphTargetIndex', morphTargetIndex)
    // Calculate the maximum possible deformation length
    let maxDeformationLength = 0;
    for (let i = 0; i < positionAttribute.count; i++) {
        const originalPosition = new THREE.Vector3().fromBufferAttribute(positionAttribute, i);
        const morphedPosition = new THREE.Vector3().fromBufferAttribute(morphAttribute, i);
        const deformationVector = morphedPosition.clone().sub(originalPosition);
        maxDeformationLength = Math.max(maxDeformationLength, deformationVector.length());
    }

    // Create a color attribute
    const colors = new Float32Array(positionAttribute.count * 3);
    const colorAttribute = new THREE.BufferAttribute(colors, 3);
    geometry.setAttribute('color', colorAttribute);

    // find max and min rgb color
    let maxRed = 0;
    let maxGreen = 0;
    let maxBlue = 0;
    let minRed = 1;
    let minGreen = 1;
    let minBlue = 1;

    let minValue = 0;
    let maxValue = 0;

    // Color each vertex based on the deformation length
    for (let i = 0; i < positionAttribute.count; i++) {
        const originalPosition = new THREE.Vector3().fromBufferAttribute(positionAttribute, i);
        const morphedPosition = new THREE.Vector3().fromBufferAttribute(morphAttribute, i);
        const deformationVector = morphedPosition.clone().sub(originalPosition);
        const normalizedLength = deformationVector.length() / maxDeformationLength;

        // Calculate the hue for the current vertex
        const hue = minHSL.h + normalizedLength * (maxHSL.h - minHSL.h);

        // Set the color of the current vertex
        const color = new THREE.Color().setHSL(hue, 1, 0.5);
        colorAttribute.setXYZ(i, color.r, color.g, color.b);

        // find max and min rgb color
        maxRed = Math.max(maxRed, color.r);
        maxGreen = Math.max(maxGreen, color.g);
        maxBlue = Math.max(maxBlue, color.b);
        minRed = Math.min(minRed, color.r);
        minGreen = Math.min(minGreen, color.g);
        minBlue = Math.min(minBlue, color.b);

        maxValue = Math.max(maxValue, normalizedLength);
        minValue = Math.min(minValue, normalizedLength);
    }

    // Update the color attribute
    colorAttribute.needsUpdate = true;

    // Update the material
    updateMeshMaterial(mesh);
    const minColor_string = `rgb(${minRed * 255}, ${minGreen * 255}, ${minBlue * 255})`;
    const minColor_str = `rgb(${maxRed * 255}, ${maxGreen * 255}, ${maxBlue * 255})`;
    console.log(minColor_string, minColor_str)

    useColorStore.getState().setMin(minValue);
    useColorStore.getState().setMax(maxValue);
    // set min color by converting it to string
    useColorStore.getState().setMinColor(minColor.getStyle());
    // set max color by converting it to string
    useColorStore.getState().setMaxColor(maxColor.getStyle());
}