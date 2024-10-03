import {FileType, Message} from "../../flatbuffers/wsock";
import {useNodeEditorStore} from "../../state/useNodeEditorStore";

export function receive_procedure(message: Message) {
    console.log('receive_procedure');
    console.log('Instance ID:', message.instanceId());
    //message.serverReply()?.
    let fileObject = message.serverReply()?.fileObject();
    if (!fileObject) {
        console.error("No file object found in the message");
        return;
    }

    let node_id = fileObject.procedureParent()?.procedureIdString();
    if (!node_id) {
        console.error("No procedure ID found in the message");
        return;
    }
    // get the originating procedure node
    const nodes = useNodeEditorStore.getState().nodes;
    const parent_node = nodes.find(node => node.id === node_id);

    let i = nodes.length + 1;
    let x = parent_node?.position?.x || 250;
    let y = parent_node?.position?.y || 0;
    let width = parent_node?.width || 200;

    // Create a new node for the procedure output product
    const node = {
        id: `file-object-${i}`, // Unique node ID
        type: 'file_object',
        position: {x: x + width + 50, y: y }, // Stagger positions vertically
        data: {
            label: fileObject.name(),
            description: fileObject.fileType().toString(),
            filepath: fileObject.filepath(),
            filetype: FileType[fileObject.fileType()].toString(),
            fileobject: fileObject,
            glbFile: fileObject.glbFile(),
            IfcSqliteFile: fileObject.ifcsqliteFile(),
            isProcedureOutput: fileObject.isProcedureOutput()
        },
    };
    // Connect the procedure output product to the procedure node
    const edges = useNodeEditorStore.getState().edges;
    const edge = {
        id: `edge-${i}`,
        source: node.id,
        target: node_id,
    };
    useNodeEditorStore.getState().setNodes([...nodes, node]);
    useNodeEditorStore.getState().setEdges([...edges, edge]);
}