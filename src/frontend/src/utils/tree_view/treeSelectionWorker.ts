// treeSelectionWorker.ts
import { getMeshFromName } from "../scene/getMeshFromName";
import { getDrawRangeByName } from "../mesh_select/getDrawRangeByName";
import type { NodeApi } from "react-arborist";
import type { CustomBatchedMesh } from "../mesh_select/CustomBatchedMesh";

type MeshAndRange = [CustomBatchedMesh, string];

function getNodesRecursive(node: NodeApi, nodes: NodeApi[]): void {
    nodes.push(node);
    if (node.children && node.children.length > 0) {
        for (let child of node.children) {
            getNodesRecursive(child, nodes);
        }
    }
}

function getMeshAndDrawRanges(nodes: NodeApi[]): MeshAndRange[] {
    const meshesAndRanges: MeshAndRange[] = [];
    for (const node of nodes) {
        const nodeName = node.data.name;
        const drawRangeData = getDrawRangeByName(nodeName);
        if (!drawRangeData) continue;

        const [key, rangeId] = drawRangeData;
        const meshNodeName = key.split("_")[2];
        const mesh = getMeshFromName(meshNodeName);
        if (mesh) {
            meshesAndRanges.push([mesh, rangeId]);
        }
    }
    return meshesAndRanges;
}

self.onmessage = (event: MessageEvent<{ ids: NodeApi[] }>) => {
    const { ids } = event.data;
    const nodes: NodeApi[] = [];

    ids.forEach((node) => getNodesRecursive(node, nodes));
    const meshesAndRanges = getMeshAndDrawRanges(nodes);

    self.postMessage({ meshesAndRanges });
};
