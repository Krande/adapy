import {Message} from "../../../flatbuffers/wsock/message";
import {useObjectInfoStore} from "../../../state/objectInfoStore";

export async function receive_mesh_info_reply (message: Message) {
    if (message.meshInfo() && message.meshInfo()?.jsonData()) {
        const json_str = message.meshInfo()?.jsonData();
        if (!json_str) {
            console.error('No JSON data found in the message');
            return;
        }
        const jsonData = JSON.parse(json_str);
        useObjectInfoStore.getState().setJsonData(jsonData);
    } else {
        useObjectInfoStore.getState().setJsonData(null);
    }
}