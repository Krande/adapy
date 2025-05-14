// state/modelCache.worker.ts
import Dexie, {Table} from "dexie";
import * as Comlink from "comlink";

export interface ModelData {
    key: string;
    hierarchy: Record<string, [string, string | number]>;
    drawRanges: Record<string, Record<number, [number, number]>>;
}

// In-worker, we’ll keep an in-memory map from key → its drawRanges
type DrawRangesMap = Record<string, Record<number, [number, number]>>;
type HierarchyMap = Record<string, [string, string | number]>;

// the shape of the pure tree we’ll return
export interface PureTreeNode {
    id: string;
    name: string;
    children: PureTreeNode[];
}

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

    async getNameFromRangeId(
        key: string,
        rangeId: string,
    ) : Promise<string | null> {
        const hierarchy = this.memoryCacheHierarchy.get(key)
        if (!hierarchy) {
            console.error("ModelWorkerAPI: No hierarchy found for key:", key);
            return null;
        }
        const node = hierarchy[rangeId];
        if (!node) return null;
        return node[0];
    }

    /**
     * Build a pure parent/child tree from your id_hierarchy JSON.
     * Does NOT touch any THREE.Object3D or meshRefs.
     */
    buildHierarchy(
        hierarchy: Record<string, [string, string | number]>
    ): PureTreeNode | null {
        // instantiate nodes
        const nodes: Record<string, PureTreeNode> = {};
        for (const [id, [name]] of Object.entries(hierarchy)) {
            nodes[id] = {id, name, children: []};
        }

        // link parents → children
        let root: PureTreeNode | null = null;
        for (const [id, [, parent]] of Object.entries(hierarchy)) {
            if (parent === "*" || parent === null) {
                root = nodes[id];
            } else {
                const p = nodes[String(parent)];
                if (p) p.children.push(nodes[id]);
            }
        }

        // natural‐sort each children array
        const sortRec = (n: PureTreeNode) => {
            n.children.sort((a, b) =>
                a.name.localeCompare(b.name, undefined, {
                    numeric: true,
                    sensitivity: "base",
                })
            );
            n.children.forEach(sortRec);
        };
        if (root) sortRec(root);

        return root;
    }
}

Comlink.expose(new ModelWorkerAPI());
