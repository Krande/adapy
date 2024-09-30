import {ProcedureStart, Message, Parameter, CommandType, TargetType, ProcedureStore} from '../../flatbuffers/wsock'
import {useNodeEditorStore} from '../../state/useNodeEditorStore';
import * as flatbuffers from "flatbuffers"; // Import the node editor Zustand store
import {webSocketHandler} from "../websocket_connector";

export function run_sequence(id: string) {
    // Find the Start Procedure node and get the connecting nodes
    const nodes = useNodeEditorStore.getState().nodes
    const edges = useNodeEditorStore.getState().edges
    const thisProcedureNode = nodes.find(node => node.id === id)
    if (!thisProcedureNode) return

    const startNodeId = thisProcedureNode.id
    const connectedEdges = edges.filter(edge => edge.target === startNodeId && edge.targetHandle === 'file_object')
    if (connectedEdges.length === 0) return
    const connectedNode = nodes.find(node => node.id === connectedEdges[0].source)
    if (!connectedNode) return

    console.log(connectedNode)

    // Trigger the procedure on the server
    let builder = new flatbuffers.Builder(1024);
    let procedure_name = builder.createString(thisProcedureNode.data?.label?.toString() || '');
    let input_variable_name = builder.createString(thisProcedureNode.data?.inputFileVar?.toString() || '');
    let filepath_str = builder.createString(connectedNode.data?.filepath?.toString() || '')

    Parameter.startParameter(builder);
    Parameter.addName(builder, input_variable_name);
    Parameter.addValue(builder, filepath_str);
    let input_file = Parameter.endParameter(builder);
    let parameters = ProcedureStart.createParametersVector(builder, [input_file]);

    ProcedureStart.startProcedureStart(builder);
    ProcedureStart.addProcedureName(builder, procedure_name);
    // add parameter to vector of parameters
    ProcedureStart.addParameters(builder, parameters);
    let procedure_start = ProcedureStart.endProcedureStart(builder);

    ProcedureStore.startProcedureStore(builder);
    ProcedureStore.addStartProcedure(builder, procedure_start);
    let procedureStore = ProcedureStore.endProcedureStore(builder);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketHandler.instance_id);
    Message.addCommandType(builder, CommandType.RUN_PROCEDURE);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    Message.addProcedureStore(builder, procedureStore);
    builder.finish(Message.endMessage(builder));

    webSocketHandler.sendMessage(builder.asUint8Array());
}