import {useServerInfoStore} from "../../../state/serverInfoStore";
import {Message} from "../../../flatbuffers/wsock/message";

export function receive_list_of_files_from_server(message: Message){
    const msg = message.unpack();
    useServerInfoStore.getState().setShowServerInfoBox(true);
}