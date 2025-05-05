// sceneHelpers/prepareLoadedModel.ts
import * as THREE from "three";
import {convert_to_custom_batch_mesh} from "../../../utils/scene/convert_to_custom_batch_mesh";
import {replaceBlackMaterials} from "../../../utils/scene/assignDefaultMaterial";
import {buildTreeFromUserData} from "../../../utils/tree_view/generateTree";
import {ModelState} from "../../../state/modelStore";
import {TreeViewState} from "../../../state/treeViewStore";
import {OptionsState} from "../../../state/optionsStore";
import {rendererRef} from "../../../state/refs";
import {FilePurpose} from "../../../flatbuffers/base";

interface PrepareLoadedModelParams {
    gltf_scene: THREE.Object3D;
    modelStore: ModelState;
    treeViewStore: TreeViewState;
    optionsStore: OptionsState;
}

function assignMorphToEdgeAlso(
    mesh: THREE.Mesh,
    edges: THREE.LineSegments
): void {
    const meshGeom = mesh.geometry as THREE.BufferGeometry;
    const meshPosAttr = meshGeom.attributes.position as THREE.BufferAttribute;
    const meshMorphs = meshGeom.morphAttributes.position!;
    const meshRel = meshGeom.morphTargetsRelative;
    const meshInf = mesh.morphTargetInfluences!;
    const meshDict = mesh.morphTargetDictionary!;

    const lineGeom = edges.geometry as THREE.BufferGeometry;
    const linePosAttr = lineGeom.attributes.position as THREE.BufferAttribute;
    let indexAttr = lineGeom.index;

    // 1) if there's no index → build one by matching each line-vertex
    if (!indexAttr) {
        // build a map: "x_y_z" → mesh-vertex-index
        const meshArr = meshPosAttr.array as Float32Array;
        const meshCount = meshArr.length / 3;
        const posMap = new Map<string, number>();
        for (let i = 0; i < meshCount; i++) {
            const key = `${meshArr[3 * i]}_${meshArr[3 * i + 1]}_${meshArr[3 * i + 2]}`;
            posMap.set(key, i);
        }

        // now map each line-vertex into that same index
        const lineArr = linePosAttr.array as Float32Array;
        const lineCount = lineArr.length / 3;
        const idx = new (meshCount > 0xffff ? Uint32Array : Uint16Array)(lineCount);
        for (let i = 0; i < lineCount; i++) {
            const key = `${lineArr[3 * i]}_${lineArr[3 * i + 1]}_${lineArr[3 * i + 2]}`;
            const mi = posMap.get(key);
            if (mi === undefined) {
                throw new Error(
                    `Cannot infer index for line-vertex ${i}: no matching mesh-vertex found`
                );
            }
            idx[i] = mi;
        }

        indexAttr = new THREE.BufferAttribute(idx, 1);
    }

    // 2) swap in the mesh’s position & morph buffers
    lineGeom.setAttribute('position', meshPosAttr);
    lineGeom.morphAttributes.position = meshMorphs;
    lineGeom.morphTargetsRelative = meshRel;
    lineGeom.setIndex(indexAttr);

    // 3) share the same influences & dictionary
    edges.morphTargetInfluences = meshInf;
    edges.morphTargetDictionary = meshDict;

    // 4) turn on morphTargets in the material
    const mat = edges.material as any;      // TS needs a little help here
    mat.morphTargets = true;
    mat.needsUpdate = true;

    // 5) flag the position attribute as updated
    meshPosAttr.needsUpdate = true;
}


export function prepareLoadedModel({
                                       gltf_scene,
                                       modelStore,
                                       treeViewStore,
                                       optionsStore,
                                   }: PrepareLoadedModelParams): void {
    modelStore.setUserData(gltf_scene.userData);
    // we'll collect all edge geometries here
    const meshesToReplace: { original: THREE.Mesh; parent: THREE.Object3D }[] = [];

    gltf_scene.traverse((object) => {
        if (object instanceof THREE.Mesh && modelStore.model_type == FilePurpose.DESIGN) {
            meshesToReplace.push({original: object, parent: object.parent!});
        } else if (object instanceof THREE.LineSegments || object instanceof THREE.Points) {
            object.layers.set(1);
        } else if (object instanceof THREE.Mesh && modelStore.model_type == FilePurpose.ANALYSIS) {
            // For analysis models, we want to set the layers for all objects
            const line_geo = object.children[0] as THREE.LineSegments;
            assignMorphToEdgeAlso(object, line_geo);
        }
    });

    for (const {original, parent} of meshesToReplace) {
        const meshName = original.name;
        const drawRangesData = gltf_scene.userData[`draw_ranges_${meshName}`] as Record<string, [number, number]>;

        const drawRanges = new Map<string, [number, number]>();
        if (drawRangesData) {
            for (const [rangeId, [start, count]] of Object.entries(drawRangesData)) {
                drawRanges.set(rangeId, [start, count]);
            }
        }

        const customMesh = convert_to_custom_batch_mesh(original, drawRanges);

        if (optionsStore.showEdges && drawRanges.size) {
            if (rendererRef.current)
                parent.add(customMesh.getEdgeOverlay(rendererRef.current));
        }

        parent.add(customMesh);
        parent.remove(original);
    }

    replaceBlackMaterials(gltf_scene);

    const boundingBox = new THREE.Box3().setFromObject(gltf_scene);
    modelStore.setBoundingBox(boundingBox);

    if (!optionsStore.lockTranslation && modelStore.model_type == FilePurpose.DESIGN) {
        const center = boundingBox.getCenter(new THREE.Vector3());
        const translation = center.clone().multiplyScalar(-1);
        if (modelStore.zIsUp) {
            const minZ = boundingBox.min.z;
            const bheight = boundingBox.max.z - minZ;
            translation.z = -minZ + bheight * 0.05;
        } else {
            const minY = boundingBox.min.y;
            const bheight = boundingBox.max.y - minY;
            translation.y = -minY + bheight * 0.05;
        }

        gltf_scene.position.add(translation);
        modelStore.setTranslation(translation);
    }

    const treeData = buildTreeFromUserData(gltf_scene.userData);
    if (treeData) treeViewStore.setTreeData(treeData);
}
