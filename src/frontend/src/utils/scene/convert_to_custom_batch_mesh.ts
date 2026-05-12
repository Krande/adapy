import {CustomBatchedMesh} from "../mesh_select/CustomBatchedMesh";
import * as THREE from "three";
import {DesignDataExtension, SimulationDataExtensionMetadata} from "@/extensions/design_and_analysis_extension";
import {usePerfStore} from "@/state/perfStore";

export function convert_to_custom_batch_mesh(original: THREE.Mesh, drawRanges: Map<string, [number, number]>, unique_key: string, is_design: boolean = true, ada_ext_data: SimulationDataExtensionMetadata | DesignDataExtension | null = null) {
    // CustomBatchedMesh holds a single base material; if the source was
    // a multi-material mesh, take the first one. Multi-material support
    // would need a wider refactor (per-group materials).
    const sourceMaterial = Array.isArray(original.material)
        ? original.material[0]
        : original.material;
    const perf = usePerfStore.getState();
    const isBeamSolid = original.userData?.feaBeamSolids === true;

    // Material substitution: if the perf store asks for Lambert, swap
    // the FEA-baked MeshStandardMaterial for a MeshLambertMaterial.
    // Lambert drops the full PBR fragment shader (metallic/roughness/
    // IBL) which is the dominant cost on Intel iGPUs for ~M-fragment
    // scenes. Copy across only the bits that survive a Standard→Lambert
    // swap (colour, transparency, vertex-colour flag). When the toggle
    // is "standard" we keep the original material untouched.
    const baseMaterial = (() => {
        if (perf.materialMode !== "lambert") return sourceMaterial;
        if (sourceMaterial instanceof THREE.MeshStandardMaterial) {
            const lam = new THREE.MeshLambertMaterial({
                color: sourceMaterial.color.clone(),
                map: sourceMaterial.map ?? null,
                transparent: sourceMaterial.transparent,
                opacity: sourceMaterial.opacity,
                vertexColors: sourceMaterial.vertexColors,
                side: sourceMaterial.side,
            });
            lam.name = sourceMaterial.name;
            return lam;
        }
        return sourceMaterial;
    })();

    const customMesh = new CustomBatchedMesh(
        original.geometry,
        baseMaterial,
        drawRanges,
        unique_key,
        is_design,
        ada_ext_data
    );

    // Copy over properties from original mesh to customMesh
    customMesh.position.copy(original.position);
    customMesh.rotation.copy(original.rotation);
    customMesh.scale.copy(original.scale);
    customMesh.name = original.name;
    customMesh.userData = original.userData;
    customMesh.castShadow = original.castShadow;
    customMesh.receiveShadow = original.receiveShadow;
    customMesh.visible = original.visible;
    customMesh.frustumCulled = original.frustumCulled;
    customMesh.renderOrder = original.renderOrder;
    customMesh.layers.mask = original.layers.mask;

    // Decide side + shading based on perf-store toggles. Solid beams
    // are watertight extrusions and benefit most from FrontSide-only
    // rendering (halves rasterised fragments). Shells genuinely need
    // DoubleSide because they may be drawn from either side. The
    // smooth-shading toggle only applies to beam solids — keeping
    // shells flat-shaded preserves the existing visual.
    const wantsBackfaceCull = perf.solidsBackfaceCull && isBeamSolid;
    const wantsFlatShading = !(perf.solidsSmoothShading && isBeamSolid);
    const targetSide = wantsBackfaceCull ? THREE.FrontSide : THREE.DoubleSide;

    const applyFlags = (mat: THREE.Material) => {
        if (mat instanceof THREE.MeshStandardMaterial) {
            mat.side = targetSide;
            mat.flatShading = wantsFlatShading;
            mat.needsUpdate = true;
        } else if (mat instanceof THREE.MeshLambertMaterial) {
            // Lambert has no flatShading prop (it's per-vertex by
            // construction), so only side is meaningful here.
            mat.side = targetSide;
            mat.needsUpdate = true;
        } else {
            console.warn(`Unexpected base material type: ${mat.type}`);
        }
    };

    if (Array.isArray(customMesh.material)) {
        customMesh.material.forEach(applyFlags);
    } else {
        applyFlags(customMesh.material);
    }

    return customMesh;
}
