import {modelStore} from "../../state/model_worker/modelStore";

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