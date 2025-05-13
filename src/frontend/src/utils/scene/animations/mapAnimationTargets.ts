import {GLTF} from "three/examples/jsm/loaders/GLTFLoader";

export function mapAnimationTargets(gltf: GLTF): Map<string, string[]> {
    // Access the raw glTF JSON structure
    const json = (gltf as any).parser?.json;
    if (!json) {
        throw new Error('Raw glTF JSON not available on parser.json');
    }

    const animDefs = json.animations as Array<any>;
    const nodeDefs = json.nodes as Array<any>;
    const result = new Map<string, string[]>();

    animDefs.forEach((animDef, idx) => {
        const animName = animDef.name || `animation_${idx}`;
        const targetNames: string[] = [];

        // Each channel references a node index in its target
        animDef.channels.forEach((channel: any) => {
            const nodeIndex = channel.target.node;
            const nodeDef = nodeDefs[nodeIndex];
            const nodeName = nodeDef?.name || `node_${nodeIndex}`;
            if (!targetNames.includes(nodeName)) {
                targetNames.push(nodeName);
            }
        });

        result.set(animName, targetNames);
    });

    return result;
}