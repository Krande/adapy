import {useServerInfoStore, ServerFileEntry} from "../../../state/serverInfoStore";
import {Message} from "../../../flatbuffers/wsock/message";
import {FileType} from "../../../flatbuffers/base";

export async function receive_list_of_files_from_server(message: Message) {
    const server = message.server();
    const entries: ServerFileEntry[] = [];
    if (server) {
        const len = server.allFileObjectsLength();
        for (let i = 0; i < len; i++) {
            const fo = server.allFileObjects(i);
            if (!fo) continue;
            entries.push({
                name: fo.name() || "",
                fileType: fo.fileType() ?? FileType.IFC,
                filepath: fo.filepath() || "",
            });
        }
    }
    const store = useServerInfoStore.getState();
    store.setServerFileObjects(entries);
    store.setServerFiles(entries.map((e) => e.name));
}
