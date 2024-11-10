import {useModelStore} from "../../state/modelStore";

export function getDrawRangeByName(name: string) {
    let scene = useModelStore.getState().scene
    if (!scene?.userData) {
        return null;
    }
    let hierarchy: Record<string, [string, string | number]> = scene?.userData["id_hierarchy"];

    let rangeId: string | null = null;

    for (let key in hierarchy) {
        if (hierarchy[key][0] === name) {
            rangeId = key
            break
        }
    }

    if (!rangeId) {
        return null
    }

    for (let key in scene.userData) {
        if (key.includes("draw_ranges")) {
            // if rangeId is found in the keys

            if (scene.userData[key].hasOwnProperty(rangeId)) {
                return [key, rangeId, scene.userData[key][rangeId][0], scene.userData[key][rangeId][1]];
            }
        }
    }
}