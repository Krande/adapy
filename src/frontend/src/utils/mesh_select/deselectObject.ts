import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import {defaultMaterial} from "../default_materials";


export function deselectObject() {
    const selectedObject = useSelectedObjectStore.getState().selectedObject;
    const originalMaterial = useSelectedObjectStore.getState().originalMaterial;
    if (selectedObject) {
        selectedObject.material = originalMaterial ? originalMaterial : defaultMaterial;
        useSelectedObjectStore.getState().setOriginalMaterial(null);
        useSelectedObjectStore.getState().setSelectedObject(null);
    }
}




