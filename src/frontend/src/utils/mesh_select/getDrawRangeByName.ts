import {useModelStore} from "../../state/modelStore";

export function getDrawRangeByName(name: string) {
    let userdata = useModelStore.getState().userdata
    if (!userdata) {
        return null;
    }
    let hierarchy: Record<string, [string, string | number]> = userdata["id_hierarchy"];

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

    for (let key in userdata) {
        if (key.includes("draw_ranges")) {
            // if rangeId is found in the keys

            if (userdata[key].hasOwnProperty(rangeId)) {
                return [key, rangeId, userdata[key][rangeId][0], userdata[key][rangeId][1]];
            }
        }
    }
}