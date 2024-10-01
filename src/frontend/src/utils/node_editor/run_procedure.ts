import {
    CommandType,
    Message,
    Parameter,
    Procedure,
    ProcedureStart,
    ProcedureStore,
    TargetType
} from '../../flatbuffers/wsock'
import {useNodeEditorStore} from '../../state/useNodeEditorStore';
import * as flatbuffers from "flatbuffers"; // Import the node editor Zustand store
import {webSocketHandler} from "../websocket_connector";


export function run_procedure(props: { id: string, data: Record<string, string | Procedure> }) {
    // Find the Start Procedure node and get the connecting nodes
    const nodes = useNodeEditorStore.getState().nodes
    const edges = useNodeEditorStore.getState().edges
    const thisProcedureNode = nodes.find(node => node.id === props.id)
    if (!thisProcedureNode) return


    // Trigger the procedure on the server
    let builder = new flatbuffers.Builder(1024);
    let procedure_name = builder.createString(thisProcedureNode.data?.label?.toString() || '');

    let parameters_list = []

    let procedure = props.data.procedure as Procedure;
    let input_file_var = procedure.inputFileVar();
    if (input_file_var) {
        const startNodeId = thisProcedureNode.id
        const connectedEdges = edges.filter(edge => edge.target === startNodeId && edge.targetHandle === 'file_object')
        if (connectedEdges.length === 0) return
        const connectedNode = nodes.find(node => node.id === connectedEdges[0].source)
        if (!connectedNode) return
        console.log(connectedNode)
        let filepath_str = builder.createString(connectedNode.data?.filepath?.toString() || '')
        let input_variable_name = builder.createString(thisProcedureNode.data?.inputFileVar?.toString() || '');
        Parameter.startParameter(builder);
        Parameter.addName(builder, input_variable_name);
        Parameter.addValue(builder, filepath_str);
        let input_file = Parameter.endParameter(builder);
        parameters_list.push(input_file)
    }

    let parameters = ProcedureStart.createParametersVector(builder, parameters_list);

    ProcedureStart.startProcedureStart(builder);
    ProcedureStart.addProcedureName(builder, procedure_name);
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