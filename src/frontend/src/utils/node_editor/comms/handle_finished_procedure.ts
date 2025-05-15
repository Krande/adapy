import {Message} from "../../../flatbuffers/wsock";
import {FileObject} from "../../../flatbuffers/base";
import {FileType} from "../../../flatbuffers/base/file-type";
import {useNodeEditorStore} from "../../../state/useNodeEditorStore";

function add_node(fileObject: FileObject) {
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
        position: {x: x + width + 50, y: y}, // Stagger positions vertically
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
    const edge = {
        id: `edge-${i}`,
        source: node.id,
        target: node_id,
    };

    // Connect the procedure output product to the procedure node
    const edges = useNodeEditorStore.getState().edges;

    useNodeEditorStore.getState().setNodes([...nodes, node]);
    useNodeEditorStore.getState().setEdges([...edges, edge]);
}


export async function handle_finished_procedure(message: Message) {
    console.log('receive_procedure');
    console.log('Instance ID:', message.instanceId());
    let fileObjectsLength = message.serverReply()?.fileObjectsLength();

    if (fileObjectsLength) {
        for (let i = 0; i < fileObjectsLength; i++) {
            let fileObject = message.serverReply()?.fileObjects(i);
            if (!fileObject) {
                console.error("No file object found in the message");
                continue;
            }
            add_node(fileObject);
        }
    }


}