import {useObjectInfoStore} from "../state/objectInfoStore";

export function toggle_info_panel() {
    useObjectInfoStore.getState().toggle();
}