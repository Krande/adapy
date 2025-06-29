import {modelStore} from "../state/model_worker/modelStore";
import {useSelectedObjectStore} from '../state/useSelectedObjectStore';
import {modelKeyMapRef} from '../state/refs';
import {CustomBatchedMesh} from './mesh_select/CustomBatchedMesh';

export async function selectGroupMembers(model_key: string, memberNames: string[]) {
    const selectedObjectStore = useSelectedObjectStore.getState();
    if (!modelStore) {
        console.error('No model store or active model key available');
        return;
    }

    try {
        // Get draw ranges for all group members from the worker
        const meshRangePairs = await modelStore.getDrawRangesByMemberNames(
            model_key,
            memberNames
        );

        if (meshRangePairs.length === 0) {
            console.warn('No draw ranges found for group members:', memberNames);
            return;
        }

        // Convert mesh names to actual mesh objects and prepare batch data
        const batchData: Array<[CustomBatchedMesh, string]> = [];
        const modelKeyMap = modelKeyMapRef.current;

        if (!modelKeyMap) {
            console.error('No model key map available');
            return;
        }
        const mesh = modelKeyMap.get(model_key);
        if (!mesh) {
            console.error(`Mesh object not found for model key: ${model_key}`);
            return;
        }

        for (const [meshName, rangeId] of meshRangePairs) {
            batchData.push([mesh as CustomBatchedMesh, rangeId])
        }

        // Clear current selection and add batch of meshes
        selectedObjectStore.clearSelectedObjects();
        if (batchData.length > 0) {
            selectedObjectStore.addBatchofMeshes(batchData);
            console.log(`Selected ${batchData.length} mesh ranges for group members`);
        }

    } catch (error) {
        console.error('Error selecting group members:', error);
    }
}