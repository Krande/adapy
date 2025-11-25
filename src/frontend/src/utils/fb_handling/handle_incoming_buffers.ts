// handleFlatbufferMessage.ts
import { Message } from '../../flatbuffers/wsock';
import { CommandType } from '../../flatbuffers/commands';
import * as flatbuffers from 'flatbuffers';
import { reply_ping } from './reply_ping';
import { update_scene_from_message } from '../scene/comms/update_scene_from_message';
import { receive_mesh_info_reply } from '../mesh_select/comms/receive_mesh_info_reply';
import { update_nodes } from '../node_editor/comms/update_nodes';
import { handle_finished_procedure } from '../node_editor/comms/handle_finished_procedure';
import { receive_list_of_files_from_server } from '../server_info/comms/receive_list_of_files_from_server';
import { useWebsocketStatusStore } from '../../state/websocketStatusStore';

export async function handleFlatbufferMessage(buffer: ArrayBuffer): Promise<void> {
  try {
    // Wrap ArrayBuffer into FlatBuffer ByteBuffer
    const byteBuffer = new flatbuffers.ByteBuffer(new Uint8Array(buffer));
    const message = Message.getRootAsMessage(byteBuffer);
    const commandType = message.commandType();

    switch (commandType) {
      case CommandType.PING:
        await reply_ping(message);
        break;

      case CommandType.UPDATE_SCENE:
        await update_scene_from_message(message);
        await update_nodes(message);
        break;

      case CommandType.MESH_INFO_REPLY:
        await receive_mesh_info_reply(message);
        break;

      case CommandType.LIST_WEB_CLIENTS:
        {
          const clients = [];
          const clientCount = message.webClientsLength();
          for (let i = 0; i < clientCount; i++) {
            const client = message.webClients(i);
            if (client) {
              clients.push({
                instanceId: client.instanceId(),
                name: client.name(),
                address: client.address(),
                port: client.port(),
                lastHeartbeat: client.lastHeartbeat(),
              });
            }
          }
          useWebsocketStatusStore.getState().setConnectedClients(clients);
          console.log('LIST_WEB_CLIENTS received:', clients);
        }
        break;

      case CommandType.SERVER_REPLY:
        const replyTo = message.serverReply()?.replyTo();
        switch (replyTo) {
          case CommandType.LIST_PROCEDURES:
            await update_nodes(message);
            break;
          case CommandType.MESH_INFO_CALLBACK:
            await receive_mesh_info_reply(message);
            break;
          case CommandType.VIEW_FILE_OBJECT:
            console.log('VIEW_FILE_OBJECT Server Reply message received');
            await update_scene_from_message(message);
            break;
          case CommandType.RUN_PROCEDURE:
            console.log('RUN_PROCEDURE Server Reply message received');
            await handle_finished_procedure(message);
            break;
          case CommandType.LIST_FILE_OBJECTS:
            await receive_list_of_files_from_server(message);
            break;
          case CommandType.GET_SERVER_INFO:
            {
              const processInfo = message.serverReply()?.processInfo();
              if (processInfo) {
                useWebsocketStatusStore.getState().setProcessInfo({
                  pid: processInfo.pid(),
                  threadId: processInfo.threadId(),
                  logFilePath: processInfo.logFilePath(),
                });
                console.log('GET_SERVER_INFO received:', {
                  pid: processInfo.pid(),
                  threadId: processInfo.threadId(),
                  logFilePath: processInfo.logFilePath(),
                });
              }
            }
            break;
          default:
            console.error(
              'Unknown Server Reply type:',
              replyTo
            );
        }
        break;

      case CommandType.ERROR:
        console.error('Server Error message received:');
        console.error(
          message.serverReply()?.error()?.message()
        );
        break;

      default:
        console.error(
          'Unknown FlatBuffer message type:',
          CommandType[commandType]
        );
    }

    console.log('Flatbuffer message received');
    console.log('Instance ID:', message.instanceId());
    console.log(
      'Command Type:',
      CommandType[message.commandType()]
    );
    console.log('Mesh Info:', message.meshInfo());
  } catch (err) {
    console.error(
      'Error handling FlatBuffer message:',
      err
    );
  }
}
