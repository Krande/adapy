import {Message} from "../../flatbuffers/wsock/message";
import {useModelStore} from "../../state/modelStore";

export const update_scene = (message: Message) => {
    console.log('Received scene update message from server');
    let fileObject = message.fileObject();
    if (!fileObject) {
        console.error("No file object found in the message");
        return;
    }
    // Get the filedata array (this is typically a Uint8Array)
    let data = fileObject.filedataArray();

    if (!data) {
        console.error("No filedata found in the file object");
        return;
    }

    const blob = new Blob([data], {type: 'model/gltf-binary'});
    const url = URL.createObjectURL(blob);

    useModelStore.getState().setModelUrl(url, null, ""); // Set the URL for the model
}