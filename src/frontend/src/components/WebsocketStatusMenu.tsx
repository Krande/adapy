import React, {useState} from 'react';
import {useWebsocketStatusStore} from '../state/websocketStatusStore';
import {webSocketAsyncHandler} from '../utils/websocket/websocket_connector_async';
import {requestServerInfo} from '../utils/websocket/requestServerInfo';
import {requestConnectedClients} from '../utils/websocket/requestConnectedClients';
import {requestShutdownServer} from '../utils/websocket/requestShutdownServer';

export function WebsocketStatusMenu() {
    const {connected, toggleShowInfoBox} = useWebsocketStatusStore();

    const handleInfoClick = () => {
        if (connected) {
            requestServerInfo();
            requestConnectedClients();
        }
        toggleShowInfoBox();
    };

    return (
        <div className="relative">
            <button
                className="flex items-center justify-center cursor-pointer"
                onClick={handleInfoClick}
                title={connected ? 'WebSocket connected - Click for info' : 'WebSocket disconnected'}
            >
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5}
                     stroke="currentColor" className="w-6 h-6">
                    <path strokeLinecap="round" strokeLinejoin="round"
                          d="M8.288 15.038a5.25 5.25 0 0 1 7.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0M12.53 18.22l-.53.53-.53-.53a.75.75 0 0 1 1.06 0Z"/>
                </svg>
                <div
                    className="absolute -top-1 -right-1 w-3 h-3 rounded-full border-2 border-white"
                    style={{backgroundColor: connected ? '#22c55e' : '#ef4444'}}
                />
            </button>
        </div>
    );
}

export function WebsocketStatusBox() {
    const [showClientsList, setShowClientsList] = useState(false);
    const {
        connected,
        frontendId,
        processInfo,
        connectedClients,
        logFilePath,
    } = useWebsocketStatusStore();

    const handleKillServer = () => {
        if (connected) {
            requestShutdownServer();
        }
    };

    return (
        <div className="bg-gray-400 bg-opacity-50 rounded p-2 min-w-80 pointer-events-auto">
            <h2 className="font-bold">WebSocket Status</h2>
            <div className="text-xs text-gray-800 space-y-2 mb-2">
                <div className="border-b border-gray-300 pb-2">
                    <div className="font-medium mb-1">Frontend Instance</div>
                    <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-xs truncate flex-1" title={String(frontendId)}>
                            ID: {frontendId || webSocketAsyncHandler.instance_id}
                        </span>
                    </div>
                </div>

                <div className="border-b border-gray-300 pb-2">
                    <div className="font-medium mb-1">WebSocket Server</div>
                    <div className="flex justify-between">
                        <span className="font-medium">Status:</span>
                        <span className={connected ? 'text-green-500 font-semibold' : 'text-red-700'}>
                            {connected ? 'Connected' : 'Disconnected'}
                        </span>
                    </div>
                    {connected && (
                        <div className="flex justify-between items-center mt-1">
                            <span className="font-medium">Connected Clients:</span>
                            <button
                                className="cursor-pointer text-blue-700 hover:text-blue-900 font-medium"
                                onClick={() => setShowClientsList(!showClientsList)}
                                title="Click to show/hide client list"
                            >
                                {connectedClients.length}
                                <span className="ml-1 text-xs">
                                    {showClientsList ? '▼' : '▶'}
                                </span>
                            </button>
                        </div>
                    )}
                    {showClientsList && connectedClients.length > 0 && (
                        <div className="mt-2 pl-2 border-l-2 border-blue-300">
                            <div className="text-xs text-gray-700 mb-1">Client Instances:</div>
                            {connectedClients.map((client) => (
                                <div
                                    key={client.instanceId}
                                    className={`text-xs font-mono px-2 py-1 rounded mb-1 ${
                                        client.instanceId === webSocketAsyncHandler.instance_id
                                            ? 'bg-blue-200 text-blue-900 font-semibold'
                                            : 'bg-gray-200 text-gray-800'
                                    }`}
                                    title={String(client.instanceId)}
                                >
                                    {client.instanceId === webSocketAsyncHandler.instance_id && (
                                        <span className="text-blue-700 mr-1">●</span>
                                    )}
                                    {client.name || `Client ${client.instanceId}`}
                                </div>
                            ))}
                        </div>
                    )}
                    {processInfo && (
                        <>
                            <div className="flex justify-between mt-1">
                                <span className="font-medium">Process ID:</span>
                                <span className="font-mono">{processInfo.pid}</span>
                            </div>
                            <div className="flex justify-between">
                                <span className="font-medium">Thread ID:</span>
                                <span className="font-mono">{processInfo.threadId}</span>
                            </div>
                        </>
                    )}
                    {!processInfo && connected && (
                        <div className="text-gray-700 italic mt-1">Loading server info...</div>
                    )}
                </div>
                {(logFilePath || processInfo?.logFilePath) && (
                    <div className="border-b border-gray-300 pb-2">
                        <div className="font-medium mb-1">Log File Path</div>
                        <div className="text-xs font-mono text-gray-800 break-words">
                            {logFilePath || processInfo?.logFilePath || 'N/A'}
                        </div>
                    </div>
                )}
            </div>
            {connected && (
                <button
                    className="cursor-pointer w-full text-xs font-medium px-3 py-2 rounded bg-red-600 text-white hover:bg-red-700 transition"
                    onClick={handleKillServer}
                >
                    Shutdown Server
                </button>
            )}
        </div>
    );
}

export default WebsocketStatusMenu;
