import {CommandType, Message} from '../flatbuffers/wsock'
import * as flatbuffers from "flatbuffers";

const handleFlatbufferMessage = (flatbuffers: flatbuffers.ByteBuffer) => {
    let message = Message.getRootAsMessage(flatbuffers);
    console.log('Flatbuffer message received');
    console.log('Instance ID:', message.instanceId());
    console.log('Command Type:', CommandType[message.commandType()]);
    console.log('File Object:', message.fileObject());
    console.log('Binary Data:', message.binaryData());
    console.log('Mesh Info:', message.meshInfo());
}
