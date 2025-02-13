import {Message} from "../../../flatbuffers/wsock/message";
import {useModelStore} from "../../../state/modelStore";
import * as THREE from "three";
import {useOptionsStore} from "../../../state/optionsStore";
import {convert_to_custom_batch_mesh} from "../convert_to_custom_batch_mesh";
import {useTreeViewStore} from "../../../state/treeViewStore";
import {buildTreeFromUserData} from "../../tree_view/generateTree";

export function append_to_scene_from_message(message: Message) {
    // Append GLTF model
    console.log('Adding model to existing scene');
    let three_scene = useModelStore.getState().scene;
    let showEdges = useOptionsStore.getState().showEdges
    const treeViewStore = useTreeViewStore.getState();

    if (three_scene) {
        let mesh = message.package_()?.mesh()?.unpack();

        if (mesh) {
            let indices = mesh.indices;
            let vertices = mesh.vertices;

            if (indices && vertices) {
                let geometry = new THREE.BufferGeometry();

                // Convert arrays to Float32Array and Uint16Array
                let vertexArray = new Float32Array(vertices);
                let indexArray = new Uint16Array(indices);

                // Define attributes
                geometry.setAttribute('position', new THREE.BufferAttribute(vertexArray, 3));
                geometry.setIndex(new THREE.BufferAttribute(indexArray, 1));

                // Compute normals if not provided
                geometry.computeVertexNormals();

                // Create material (adjust as needed)
                let material = new THREE.MeshStandardMaterial({color: 0x808080, metalness: 0.5, roughness: 0.5});

                // Create mesh
                let threeMesh = new THREE.Mesh(geometry, material);
                if (mesh.name)
                    threeMesh.name = mesh.name as string;

                let mesh_array_len = threeMesh.geometry.index?.array.length
                if (!mesh_array_len) {
                    console.warn("Unable to find mesh array length")
                    return
                }

                let id_hierarchy = three_scene.userData["id_hierarchy"]
                const maxKey = Math.max(...Object.keys(id_hierarchy).map(Number));
                let range_id_str = (maxKey + 1).toString();

                three_scene.userData[`draw_ranges_${threeMesh.name}`] =  { [range_id_str]: [0, mesh_array_len]}
                three_scene.userData["id_hierarchy"][range_id_str] = [threeMesh.name, 0]

                const drawRanges = new Map<string, [number, number]>();
                drawRanges.set(range_id_str, [0, mesh_array_len]);

                const customMesh = convert_to_custom_batch_mesh(threeMesh, drawRanges);

                // Add to scene
                three_scene.add(customMesh);

                if (showEdges) {
                    let edgeLine = customMesh.get_edge_lines();
                    three_scene.add(edgeLine);
                }

                // Generate the tree data and update the store
                const treeData = buildTreeFromUserData(three_scene);
                if (treeData) treeViewStore.setTreeData(treeData);

                console.log('Mesh added to scene');
            } else {
                console.warn('Invalid mesh data: missing vertices or indices');
            }
        } else {
            console.warn('No mesh found in message');
        }
    }

}