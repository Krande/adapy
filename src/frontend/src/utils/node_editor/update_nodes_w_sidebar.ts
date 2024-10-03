import { FileType, Message } from '../../flatbuffers/wsock';
import { useNodeEditorStore } from '../../state/useNodeEditorStoreWSidebar'; // Import the node editor Zustand store
import ProcedureNode from '../../components/node_editor/nodes/customProcedureNode';
import CustomFileObjectNode from '../../components/node_editor/nodes/customFileObjectNode';

// Define a type for the available node type structure
type AvailableNodeType = {
  type: string;
  label: string;
  instance: React.FC; // Store the node instance as a React component
};

// Function to update nodes and available nodes in the Zustand store
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
            position: { x: 250, y: i * 100 - 100 }, // Stagger positions vertically
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
        position: { x: 250, y: i * 100 }, // Stagger positions vertically
        data: {
          label: procedure.name(),
          description: procedure.description(),
          scriptFileLocation: procedure.scriptFileLocation(),
          inputFileVar: procedure.inputFileVar(),
          inputFileType: procedure.inputFileType(),
          exportFileType: procedure.exportFileType(),
          procedure: procedure
        },
      };
      newNodes.push(node);
    }
  }

  // Update the nodes in the Zustand state
  useNodeEditorStore.getState().setNodes(newNodes);

  // Create the available node types with instances for the Sidebar
  const availableNodes: AvailableNodeType[] = [
    {
      type: 'procedure',
      label: 'Procedure Node',
      instance: ProcedureNode,
    },
    {
      type: 'file_object',
      label: 'File Object Node',
      instance: CustomFileObjectNode,
    },
  ];

  // Update the available nodes in the Zustand state
  useNodeEditorStore.getState().setAvailableNodes(availableNodes);
};
