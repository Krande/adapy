import {FileType, Message} from '../../flatbuffers/wsock';
import {useNodeEditorStore} from '../../state/useNodeEditorStore'; // Import the node editor Zustand store



export const update_nodes = (message: Message) => {
    // Get the ProcedureStore from the received message
    const procedureStore = message.procedureStore();
    if (!procedureStore) return;

    // Check if there are any procedures in the ProcedureStore
    const proceduresLength = procedureStore.proceduresLength();
    if (!proceduresLength) return;

    const newNodes = [];

    // Create a list of nodes from the scene file objects
    const server = message.server();
    if (server) {
        const file_objects_length = server.allFileObjectsLength();
        if (file_objects_length) {
            for (let i = 0; i < file_objects_length; i++) {
                const fileObject = message.server()?.allFileObjects(i);
                if (fileObject) {
                    // Create a new node for each file object
                    const node = {
                        id: `file-object-${i}`, // Unique node ID
                        type: 'file_object',
                        position: {x: 250, y: i * 100 - 100}, // Stagger positions vertically
                        data: {
                            label: fileObject.name(),
                            description: fileObject.fileType().toString(),
                            filepath: fileObject.filepath(),
                            filetype: FileType[fileObject.fileType()].toString(),
                            fileobject: fileObject,
                            glbFile: fileObject.glbFile(),
                            IfcSqliteFile: fileObject.ifcsqliteFile()
                        },
                    };
                    newNodes.push(node);
                }
            }
        }
    }

    // Create a list of nodes from the procedures
    for (let i = 0; i < proceduresLength; i++) {
        const procedure = procedureStore.procedures(i);
        if (procedure) {
            // Create a new node for each procedure
            const node = {
                id: `procedure-${i}`, // Unique node ID
                type: 'procedure',
                position: {x: 250, y: i * 100}, // Stagger positions vertically
                data: {
                    label: procedure.name(),
                    description: procedure.description(),
                    scriptFileLocation: procedure.scriptFileLocation(),
                    inputFileVar: procedure.inputFileVar(),
                    inputFileType: procedure.inputFileType(),
                    exportFileType: procedure.exportFileType(),
                },
            };
            newNodes.push(node);
        }
    }

    // Update the nodes in the Zustand state
    useNodeEditorStore.getState().setNodes(newNodes);
};
