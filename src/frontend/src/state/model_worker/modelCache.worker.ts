// state/modelCache.worker.ts
import Dexie, {Table} from "dexie";
import * as Comlink from "comlink";
import {TreeNodeData} from "../../components/tree_view/CustomNode";

export interface ModelData {
    key: string;
    hierarchy: Record<string, [string, string | number]>;
    drawRanges: Record<string, Record<number, [number, number]>>;
}

// In-worker, we’ll keep an in-memory map from key → its drawRanges
type DrawRangesMap = Record<string, Record<number, [number, number]>>;
type HierarchyMap = Record<string, [string, string | number]>;

class ModelWorkerAPI {
    private db: Dexie;
    private models!: Table<ModelData, string>;
    private memoryCacheDrawRange = new Map<string, DrawRangesMap>();
    private memoryCacheHierarchy = new Map<string, HierarchyMap>();

    constructor() {
        this.db = new Dexie("ModelCacheDB");
        this.db.version(1).stores({models: "&key"});
        this.models = this.db.table("models") as Table<ModelData, string>;
    }

    async add(key: string, hierarchy: Record<string, [string, string | number]>, drawRanges: Record<string, Record<number, [number, number]>>): Promise<void> {
        await this.models.put({key, hierarchy, drawRanges});
        // keep in memory for super-fast lookups
        this.memoryCacheDrawRange.set(key, drawRanges);
        this.memoryCacheHierarchy.set(key, hierarchy);
    }

    async get(key: string): Promise<ModelData | undefined> {
        return this.models.get(key);
    }

    async remove(key: string): Promise<void> {
        await this.models.delete(key);
    }

    async listKeys(): Promise<string[]> {
        return this.models.toCollection().primaryKeys();
    }

    /**
     * Off‐load the mesh/face → draw‐range logic entirely into the worker.
     * Returns [rangeId, start, length] or null if none matches.
     */
    async getDrawRange(
        key: string,
        meshName: string,
        faceIndex: number
    ): Promise<[string, number, number] | null> {
        const drawRanges = this.memoryCacheDrawRange.get(key)
        if (!drawRanges) {
            console.error("ModelWorkerAPI: No drawRanges found for key:", key);
            return null;
        }
        const rangesForMesh = drawRanges[meshName];
        if (!rangesForMesh) return null;

        const faceStart = faceIndex * 3;
        for (const [rangeId, [start, length]] of Object.entries(rangesForMesh)) {
            if (faceStart >= start && faceStart < start + length) {
                return [rangeId, start, length];
            }
        }
        return null;
    }

    async getPointId(
        key: string,
        meshName: string,
        pointIndex: number): Promise<[string, number, number] | null> {
        const drawRanges = this.memoryCacheDrawRange.get(key)
        if (!drawRanges) {
            console.error("ModelWorkerAPI: No drawRanges found for key:", key);
            return null;
        }
        const rangesForMesh = drawRanges[meshName];
        if (!rangesForMesh) return null;

        const faceStart = pointIndex;
        for (const [rangeId, [start, length]] of Object.entries(rangesForMesh)) {
            if (faceStart >= start && faceStart < start + length) {
                return [rangeId, start, length];
            }
        }
        return null;
    }

    async getPointRangeByRangeId(
        key: string,
        meshName: string,
        rangeId: string
    ): Promise<[number, number] | null> {
        const drawRanges = this.memoryCacheDrawRange.get(key);
        if (!drawRanges) {
            console.error("ModelWorkerAPI: No drawRanges found for key:", key);
            return null;
        }
        const rangesForMesh = drawRanges[meshName];
        if (!rangesForMesh) return null;
        const entry = (rangesForMesh as any)[rangeId];
        if (!entry) return null;
        const [start, length] = entry as [number, number];
        return [start, length];
    }

    async getNameFromRangeId(
        key: string,
        rangeId: string,
    ): Promise<string | null> {
        const hierarchy = this.memoryCacheHierarchy.get(key)
        if (!hierarchy) {
            console.error("ModelWorkerAPI: No hierarchy found for key:", key);
            return null;
        }
        const node = hierarchy[rangeId];
        if (!node) return null;
        return node[0];
    }

    /** Helper: build a map of elementId → meshName */
    private buildElementToMeshMap(key: string): Map<string, string> {
        const drawRanges = this.memoryCacheDrawRange.get(key) ?? {};
        const map = new Map<string, string>();
        for (const [meshName, ranges] of Object.entries(drawRanges)) {
            for (const elementId of Object.keys(ranges)) {
                map.set(elementId, meshName);
            }
        }
        return map;
    }

    /**
     * Build a pure parent/child tree from your id_hierarchy JSON.
     * Does NOT touch any THREE.Object3D or meshRefs.
     */
    buildHierarchy(
        key: string,
        hierarchy: Record<string, [string, string]>,
        start_id: number
    ): TreeNodeData | null {
        // instantiate nodes
        const nodes: Record<string, TreeNodeData> = {};
        const elementToMesh = this.buildElementToMeshMap(key);
        let id = start_id + 1;
        let id_rangeIdMap = new Map<string, string>();
        for (const [rangeId, [name]] of Object.entries(hierarchy)) {
            // convert id to string
            const string_id = String(id);
            nodes[string_id] = {
                id: string_id,
                name: name,
                children: [],
                rangeId: rangeId,
                model_key: key,
                node_name: elementToMesh.get(rangeId) ?? null,
            };
            id_rangeIdMap.set(rangeId, string_id);
            id++;
        }

        // link parents → children
        let root: TreeNodeData | null = null;
        for (const [rangeId, [, parent]] of Object.entries(hierarchy)) {
            // convert id to string
            const string_id = id_rangeIdMap.get(rangeId);
            if (!string_id) {
                console.warn(
                    `ModelWorkerAPI: No string_id found for ${rangeId} (${parent})`
                );
                continue;
            }
            if (parent === "*" || parent === null) {
                root = nodes[string_id];
            } else {
                let parent_id = id_rangeIdMap.get(parent);
                if (!parent_id) {
                    console.warn(
                        `ModelWorkerAPI: No parent found for ${string_id} (${parent})`
                    );
                    continue;
                }
                const p = nodes[parent_id];
                if (p) p.children.push(nodes[string_id]);
            }
        }

        // natural‐sort each children array
        const sortRec = (n: TreeNodeData) => {
            n.children.sort((a, b) => {
                try {
                    return a.name.localeCompare(b.name, undefined, {
                        numeric: true,
                        sensitivity: "base",
                    });
                } catch (e) {
                    if (e instanceof TypeError) {
                        return 0;
                    }
                    throw e;
                }
            });
            n.children.forEach(sortRec);
        };
        if (root) sortRec(root);

        return root;
    }

    /**
     * Find all draw ranges for elements that match the given member names
     * Returns array of [meshName, rangeId] pairs
     */
    async getDrawRangesByMemberNames(
        key: string,
        memberNames: string[]
    ): Promise<Array<[string, string]>> {
        const hierarchy = this.memoryCacheHierarchy.get(key);
        const drawRanges = this.memoryCacheDrawRange.get(key);

        if (!hierarchy || !drawRanges) {
            console.error("ModelWorkerAPI: No hierarchy or drawRanges found for key:", key);
            return [];
        }

        const results: Array<[string, string]> = [];
        const elementToMesh = this.buildElementToMeshMap(key);

        // Find all rangeIds that match the member names
        for (const [rangeId, [name]] of Object.entries(hierarchy)) {
            if (memberNames.includes(name)) {
                const meshName = elementToMesh.get(rangeId);
                if (meshName) {
                    results.push([meshName, rangeId]);
                }
            }
        }

        return results;
    }

}

Comlink.expose(new ModelWorkerAPI());
