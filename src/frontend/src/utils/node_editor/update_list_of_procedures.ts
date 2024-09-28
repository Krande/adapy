import {Message} from '../../flatbuffers/wsock';
import {useNodeEditorStore} from '../../state/useNodeEditorStore'; // Import the node editor Zustand store

export const update_list_of_procedures = (message: Message) => {
    // Get the ProcedureStore from the received message
    const procedureStore = message.procedureStore();
    if (!procedureStore) return;

    // Check if there are any procedures in the ProcedureStore
    const proceduresLength = procedureStore.proceduresLength();
    if (!proceduresLength) return;

    // Create a list of nodes from the scene file objects

    // Create a list of nodes from the procedures
    const newNodes = [];
    for (let i = 0; i < proceduresLength; i++) {
        const procedure = procedureStore.procedures(i);
        if (procedure) {
            // Create a new node for each procedure
            const node = {
                id: `procedure-${i}`, // Unique node ID
                type: 'default',
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
