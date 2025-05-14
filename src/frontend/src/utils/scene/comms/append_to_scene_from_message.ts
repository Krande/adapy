import {Message} from "../../../flatbuffers/wsock/message";
import {useModelState} from "../../../state/modelState";
import * as THREE from "three";
import {useOptionsStore} from "../../../state/optionsStore";
import {convert_to_custom_batch_mesh} from "../convert_to_custom_batch_mesh";
import {useTreeViewStore} from "../../../state/treeViewStore";
import {buildTreeFromUserData} from "../../tree_view/generateTree";
import {rendererRef, sceneRef} from "../../../state/refs";
import {MeshT} from "../../../flatbuffers/meshes/mesh";
import {modelStore} from "../../../state/model_worker/modelStore";

export function add_mesh_to_scene(mesh: MeshT) {
    let three_scene = sceneRef.current;

    let showEdges = useOptionsStore.getState().showEdges;

    if (!three_scene) {
        return;
    }
    let indices = mesh.indices;
    let vertices = mesh.vertices;

    if (!(indices && vertices)) {
        console.warn("Invalid mesh data: missing vertices or indices");
        return;
    }
    let geometry = new THREE.BufferGeometry();

    // Convert arrays to Float32Array and Uint16Array
    let vertexArray = new Float32Array(vertices);
    let indexArray = new Uint16Array(indices);

    // Define attributes
    geometry.setAttribute("position", new THREE.BufferAttribute(vertexArray, 3));
    geometry.setIndex(new THREE.BufferAttribute(indexArray, 1));

    // Compute normals if not provided
    geometry.computeVertexNormals();

    // Create material (adjust as needed)
    let material = new THREE.MeshStandardMaterial({
        color: 0x808080,
        metalness: 0.5,
        roughness: 0.5,
    });

    // Create mesh
    let threeMesh = new THREE.Mesh(geometry, material);
    if (mesh.name) threeMesh.name = mesh.name as string;

    let mesh_array_len = threeMesh.geometry.index?.array.length;
    if (!mesh_array_len) {
        console.warn("Unable to find mesh array length");
        return;
    }
    let userdata = useModelState.getState().userdata;
    if (!userdata) {
        console.warn("No userdata found");
        userdata = {"id_hierarchy": {}};
    }
    let id_hierarchy = userdata["id_hierarchy"];
    const maxKey = Math.max(...Object.keys(id_hierarchy).map(Number));
    let range_id_str = (maxKey + 1).toString();

    userdata[`draw_ranges_${threeMesh.name}`] = {
        [range_id_str]: [0, mesh_array_len],
    };
    userdata["id_hierarchy"][range_id_str] = [threeMesh.name, 0];

    const drawRanges = new Map<string, [number, number]>();
    drawRanges.set(range_id_str, [0, mesh_array_len]);
    const hash = mesh.name
    const customMesh = convert_to_custom_batch_mesh(threeMesh, drawRanges, hash);
    // Add to scene
    three_scene.add(customMesh);

    if (showEdges && rendererRef.current) {
        three_scene.add(customMesh.getEdgeOverlay(rendererRef.current));
    }
    console.log("Mesh added to scene");
}

export function append_to_scene_from_message(message: Message) {
    // Append GLTF model
    console.log("Adding model to existing scene");

    let mesh = message.package_()?.mesh()?.unpack();
    if (mesh) {
        add_mesh_to_scene(mesh)
    }


}
