// state/modelStore.ts
import * as Comlink from "comlink";
// inline the worker so it’s embedded in the bundle
import Worker from "./modelCache.worker.ts?worker&inline";
import {TreeNodeData} from "../../components/tree_view/CustomNode";

// This must match what your worker returns via Comlink.expose
export interface ModelData {
    key: string;
    hierarchy: Record<string, [string, string | number]>;
    drawRanges: Record<string, Record<number, [number, number]>>;
}

export interface ModelStoreAPI {
    add(
        key: string,
        hierarchy: Record<string, [string, string | number]>,
        drawRanges: Record<string, Record<number, [number, number]>>
    ): Promise<void>;

    get(key: string): Promise<ModelData | undefined>;

    remove(key: string): Promise<void>;

    listKeys(): Promise<string[]>;

    // <— no "?" here! always present
    buildHierarchy(
        key: string,
        hierarchy: Record<string, [string, string | number]>,
        start_id: number
    ): Promise<TreeNodeData | null>;

    getDrawRange(
        key: string,
        meshName: string,
        faceIndex: number
    ): Promise<[string, number, number] | null>;

    getPointId(
        key: string,
        meshName: string,
        pointIndex: number
    ): Promise<[string, number, number] | null>;

    getNameFromRangeId(
        key: string,
        rangeId: string
    ): Promise<string | null>;

    // New method for group selection
    getDrawRangesByMemberNames(
        key: string,
        memberNames: string[]
    ): Promise<Array<[string, string]>>; // Returns [meshName, rangeId] pairs
}

const worker = new Worker();
export const modelStore = Comlink.wrap<ModelStoreAPI>(worker);
