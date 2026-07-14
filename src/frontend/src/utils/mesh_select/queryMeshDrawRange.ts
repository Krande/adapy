import {modelStore} from "@/state/model_worker/modelStore";

// an async helper—still off the main thread:
export async function queryMeshDrawRange(
    key: string,
    meshName: string,
    faceIndex: number
): Promise<[string, number, number] | null> {
    return await modelStore.getDrawRange(key, meshName, faceIndex);
}

// an async helper—still off the main thread:
export async function queryPointDrawRange(
    key: string,
    meshName: string,
    pointIndex: number
): Promise<[string, number, number] | null> {
    return await modelStore.getPointId(key, meshName, pointIndex);
}

export async function queryNameFromRangeId(
    key: string,
    rangeId: string
): Promise<string | null> {
    return await modelStore.getNameFromRangeId(key, rangeId);
}

export async function queryPointRangeByRangeId(
    key: string,
    meshName: string,
    rangeId: string
): Promise<[number, number] | null> {
    return await modelStore.getPointRangeByRangeId(key, meshName, rangeId);
}

// Per-face clickable regions (opt-in face_ranges_node): resolve a clicked triangle to its source
// face region (STEP/IFC entity id + sequential index within the solid), or null when the model
// carries no face regions / the triangle isn't in one.
export async function queryFaceInfo(
    key: string,
    meshName: string,
    faceIndex: number
): Promise<{rangeId: string; faceId: number; seq: number; start: number; length: number} | null> {
    return await modelStore.getFaceInfo(key, meshName, faceIndex);
}

export async function queryHasFaceRanges(key: string): Promise<boolean> {
    return await modelStore.hasFaceRanges(key);
}