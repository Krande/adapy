// EdgeShaderMaterial.ts
import * as THREE from 'three';
import {EdgeShader} from './EdgeShader';

export class EdgeShaderMaterial extends THREE.ShaderMaterial {
    constructor() {
        super({
            uniforms: THREE.UniformsUtils.clone(EdgeShader.uniforms),
            vertexShader: EdgeShader.vertexShader,
            fragmentShader: EdgeShader.fragmentShader,
            lights: true, // Enable lighting
        });

        // Default material properties
        this.color = new THREE.Color(0xffffff);
        this.emissive = new THREE.Color(0x000000);
        this.flatShading = true;
    }

    // Standard material properties
    color: THREE.Color;
    emissive: THREE.Color;

    // Getter and setter for diffuse color
    get diffuse(): THREE.Color {
        return this.uniforms.diffuse.value;
    }

    set diffuse(value: THREE.Color) {
        this.uniforms.diffuse.value = value;
    }

    // Getter and setter for edge color
    get edgeColor(): THREE.Color {
        return this.uniforms.edgeColor.value;
    }

    set edgeColor(value: THREE.Color) {
        this.uniforms.edgeColor.value = value;
    }

    // Getter and setter for edge thickness
    get edgeThickness(): number {
        return this.uniforms.edgeThickness.value;
    }

    set edgeThickness(value: number) {
        this.uniforms.edgeThickness.value = value;
    }

    // Getter and setter for edge strength
    get edgeStrength(): number {
        return this.uniforms.edgeStrength.value;
    }

    set edgeStrength(value: number) {
        this.uniforms.edgeStrength.value = value;
    }

    // Getter and setter for show edges
    get showEdges(): boolean {
        return this.uniforms.showEdges.value;
    }

    set showEdges(value: boolean) {
        this.uniforms.showEdges.value = value;
    }
}
