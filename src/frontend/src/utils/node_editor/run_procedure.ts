import {
    CommandType,
    Message,
    Parameter, ParameterT, ParameterType,
    Procedure,
    ProcedureStart,
    ProcedureStore,
    TargetType, Value
} from '../../flatbuffers/wsock'
import {useNodeEditorStore} from '../../state/useNodeEditorStore';
import * as flatbuffers from "flatbuffers"; // Import the node editor Zustand store
import {webSocketHandler} from "../websocket_connector";
import {Builder} from "flatbuffers";

function extract_input_params(builder: Builder, params: string[], procedure: Procedure) {
    // the param strings are div keys in the form of 'param-<procedure_name>-<index>'
    // we need to first get the parent div, then get the input value from the input element

    let procedureT = procedure.unpack()
    let parameter_name_map: Record<string, ParameterT> = {}

    for (let i = 0; i < procedureT.parameters.length; i++) {
        let parameter = procedureT.parameters[i]
        let name = parameter.name?.toString() || ''
        parameter_name_map[name] = parameter
    }

    let input_params: number[] = []
    for (let param of params) {
        let param_div = document.getElementById(param)
        if (!param_div) continue
        let param_name = param_div.getElementsByTagName('span')[0].innerText
        let parameter = parameter_name_map[param_name]
        let param_type: number = parameter.type
        let param_type_str = ParameterType[param_type]
        let param_input = param_div.getElementsByTagName('input')
        // if param_input length is > 1, then it is a tuple
        let param_name_str = builder.createString(param_name)
        if (param_input.length == 1) {
            let input_value = param_input[0].value
            let input_value_buf = null
            if (parameter.type === ParameterType.STRING) {
                let input_value_str = builder.createString(input_value)
                Value.startValue(builder);
                Value.addStringValue(builder, input_value_str);
                input_value_buf = Value.endValue(builder);
            } else if (parameter.type === ParameterType.FLOAT) {
                let input_value_float = parseFloat(input_value)
                Value.startValue(builder);
                Value.addFloatValue(builder, input_value_float);
                input_value_buf = Value.endValue(builder);
            } else if (parameter.type === ParameterType.INTEGER) {
                let input_value_int = parseInt(input_value)
                Value.startValue(builder);
                Value.addIntegerValue(builder, input_value_int);
                input_value_buf = Value.endValue(builder);
            } else if (parameter.type === ParameterType.BOOLEAN) {
                let input_value_bool = input_value === 'true'
                Value.startValue(builder);
                Value.addBooleanValue(builder, input_value_bool);
                input_value_buf = Value.endValue(builder);
            } else {
                console.log('Unknown parameter type: ', param_type_str)
                continue
            }

            Parameter.startParameter(builder);
            Parameter.addName(builder, param_name_str);
            Parameter.addValue(builder, input_value_buf);
            Parameter.addType(builder, param_type);
            let input_param = Parameter.endParameter(builder);
            input_params.push(input_param)
            continue
        } else if (param_input.length > 1) {
            let tuple_values = [];
            let array_value_type = parameter.defaultValue?.arrayValueType;

            for (let input of param_input) {
                let input_value = input.value;

                // Start building a Value based on the arrayValueType (STRING, FLOAT, etc.)
                Value.startValue(builder);

                if (array_value_type === ParameterType.STRING) {
                    let input_value_str = builder.createString(input_value);
                    Value.addStringValue(builder, input_value_str);
                } else if (array_value_type === ParameterType.FLOAT) {
                    Value.addFloatValue(builder, parseFloat(input_value));
                } else if (array_value_type === ParameterType.INTEGER) {
                    Value.addIntegerValue(builder, parseInt(input_value));
                } else if (array_value_type === ParameterType.BOOLEAN) {
                    Value.addBooleanValue(builder, input_value === "true");
                }

                // End building Value and add to the tuple_values array
                let input_value_buf = Value.endValue(builder);
                tuple_values.push(input_value_buf);
            }

            let tuple_values_vector = Value.createArrayValueVector(builder, tuple_values);
            // Create a vector for tuple_values
            Value.startValue(builder);
            Value.addArrayValue(builder, tuple_values_vector);
            if (array_value_type){
                Value.addArrayValueType(builder, array_value_type);
            }
            let array_value = Value.endValue(builder);

            // Start building the Parameter with the array of values
            Parameter.startParameter(builder);
            Parameter.addName(builder, param_name_str);
            Parameter.addValue(builder, array_value);
            Parameter.addType(builder, ParameterType.ARRAY); // Indicate that this is an array type parameter

            let input_param = Parameter.endParameter(builder);
            input_params.push(input_param);
            continue;
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

    let parameters_list: number[] = []

    let procedure = props.data.procedure as Procedure;
    if (!procedure.isComponent()) {
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
    let input_params = extract_input_params(builder, props.data.paramids as string[], procedure);
    if (input_params)
        parameters_list.push(...input_params)

    let parameters = ProcedureStart.createParametersVector(builder, parameters_list);
    let procedure_id_str = builder.createString(props.id.toString())

    ProcedureStart.startProcedureStart(builder);
    ProcedureStart.addProcedureName(builder, procedure_name);
    ProcedureStart.addProcedureIdString(builder, procedure_id_str);
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