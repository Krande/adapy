import {
    CommandType,
    Message,
    Parameter, ParameterT,
    Procedure,
    ProcedureStart,
    ProcedureStore,
    TargetType, Value
} from '../../flatbuffers/wsock'
import {useNodeEditorStore} from '../../state/useNodeEditorStore';
import * as flatbuffers from "flatbuffers"; // Import the node editor Zustand store
import {webSocketHandler} from "../websocket_connector";
import {Builder} from "flatbuffers";

function extract_input_params(builder: Builder, params: string[]) {
    // the param strings are div keys in the form of 'param-<procedure_name>-<index>'
    // we need to first get the parent div, then get the input value from the input element

    let input_params: number[] = []
    for (let param of params) {
        let param_div = document.getElementById(param)
        if (!param_div) return
        let param_name = param_div.getElementsByTagName('span')[0].innerText
        let param_input = param_div.getElementsByTagName('input')
        // if param_input length is > 1, then it is a tuple
        let param_name_str = builder.createString(param_name)
        if (param_input.length == 1) {
            let input_value = param_input[0].value
            let input_value_str = builder.createString(input_value)
            Parameter.startParameter(builder);
            Parameter.addName(builder, param_name_str);
            Parameter.addValue(builder, input_value_str);
            let input_param = Parameter.endParameter(builder);
            input_params.push(input_param)
            continue
        } else if (param_input.length > 1) {
            let tuple_values = []
            for (let input of param_input) {
                let input_value = input.value
                let input_value_str = builder.createString(input_value)
                tuple_values.push(input_value_str)
            }
            let tuple_values_vector = ProcedureStart.createTupleValuesVector(builder, tuple_values)
            Parameter.startParameter(builder);
            Parameter.addName(builder, param_name_str);
            Parameter.addValue(builder, tuple_values_vector);
            let input_param = Parameter.endParameter(builder);
            input_params.push(input_param)
            continue
        }

        // if param_div is an input element, get the value directly
        if (param_div.tagName === 'INPUT') {
            Parameter.startParameter(builder);

            let input_param = Parameter.endParameter(builder);
            input_params.push(input_param)
            continue
        }
        let input = param_div.getElementsByTagName('input')[0]
        if (!input) return

        input_params.push()
    }


    return input_params

}

export function run_procedure(props: { id: string, data: Record<string, string | Procedure | object> }) {
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
        const connectedEdges = edges.filter(edge => edge.target.startsWith('file-object'))
        if (connectedEdges.length === 0) return
        const connectedNode = nodes.find(node => node.id === connectedEdges[0].target)
        if (!connectedNode) return
        console.log(connectedNode)
        let filepath_str = builder.createString(connectedNode.data?.filepath?.toString() || '')
        let input_variable_name = builder.createString(thisProcedureNode.data?.inputFileVar?.toString() || '');
        Value.startValue(builder);
        Value.addStringValue(builder, filepath_str);
        let filepath = Value.endValue(builder);
        Parameter.startParameter(builder);
        Parameter.addName(builder, input_variable_name);
        Parameter.addValue(builder, filepath);
        let input_file = Parameter.endParameter(builder);
        parameters_list.push(input_file)
    }
    //let input_params = extract_input_params(builder, props.data.paramids as string[]);
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