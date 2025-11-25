import React, {useState} from 'react';
import {useWebsocketStatusStore} from '../state/websocketStatusStore';
import {webSocketAsyncHandler} from '../utils/websocket/websocket_connector_async';
import {requestServerInfo} from '../utils/websocket/requestServerInfo';
import {requestConnectedClients} from '../utils/websocket/requestConnectedClients';
import {requestShutdownServer} from '../utils/websocket/requestShutdownServer';

export function WebsocketStatusMenu() {
    const [menuOpen, setMenuOpen] = useState(false);
    const [showClientsList, setShowClientsList] = useState(false);

    const {
        connected,
        frontendId,
        processInfo,
        connectedClients,
        logFilePath
    } = useWebsocketStatusStore();

    const handleInfoClick = () => {
        if (!menuOpen && connected) {
            requestServerInfo();
            requestConnectedClients();
        }
        setMenuOpen(!menuOpen);
    };

    const handleKillServer = () => {
        if (connected) {
            requestShutdownServer();
            setMenuOpen(false);
        }
    };

    return (
        <div className="flex items-center gap-2 relative">
            <div
                className="w-3 h-3 rounded-full cursor-pointer"
                title={connected ? 'WebSocket connected' : 'WebSocket disconnected'}
                style={{backgroundColor: connected ? '#22c55e' : '#ef4444'}}
                onClick={handleInfoClick}
            />
            <button
                className="cursor-pointer w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold text-white bg-gray-500 hover:bg-gray-600 transition"
                onClick={handleInfoClick}
                title="Server info"
            >
                i
            </button>
            {menuOpen && (
                <>
                    <div
                        className="fixed inset-0 z-20"
                        onClick={() => setMenuOpen(false)}
                    />
                    <div
                        className="absolute top-full left-0 mt-2 w-80 bg-white border border-gray-200 rounded-lg shadow-lg z-30 p-3"
                    >
                        <div className="text-sm font-semibold text-gray-700 mb-2">WebSocket Status</div>
                        <div className="text-xs text-gray-600 space-y-2 mb-3">
                            <div className="border-b border-gray-200 pb-2">
                                <div className="font-medium text-gray-700 mb-1">Frontend Instance</div>
                                <div className="flex items-center justify-between gap-2">
                                    <span className="font-mono text-xs truncate flex-1" title={String(frontendId)}>
                                        ID: {frontendId || webSocketAsyncHandler.instance_id}
                                    </span>
                                </div>
                            </div>

                            <div className="border-b border-gray-200 pb-2">
                                <div className="font-medium text-gray-700 mb-1">WebSocket Server</div>
                                <div className="flex justify-between">
                                    <span className="font-medium">Status:</span>
                                    <span className={connected ? 'text-green-600' : 'text-red-600'}>
                                        {connected ? 'Connected' : 'Disconnected'}
                                    </span>
                                </div>
                                {connected && (
                                    <div className="flex justify-between items-center mt-1">
                                        <span className="font-medium">Connected Clients:</span>
                                        <button
                                            className="cursor-pointer text-blue-600 hover:text-blue-800 font-medium"
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
                                    <div className="mt-2 pl-2 border-l-2 border-blue-200">
                                        <div className="text-xs text-gray-500 mb-1">Client Instances:</div>
                                        {connectedClients.map((client) => (
                                            <div
                                                key={client.instanceId}
                                                className={`text-xs font-mono px-2 py-1 rounded mb-1 ${
                                                    client.instanceId === webSocketAsyncHandler.instance_id
                                                        ? 'bg-blue-100 text-blue-900 font-semibold'
                                                        : 'bg-gray-50 text-gray-700'
                                                }`}
                                                title={String(client.instanceId)}
                                            >
                                                {client.instanceId === webSocketAsyncHandler.instance_id && (
                                                    <span className="text-blue-600 mr-1">●</span>
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
                                    <div className="text-gray-400 italic mt-1">Loading server info...</div>
                                )}
                            </div>
                            {(logFilePath || processInfo?.logFilePath) && (
                                <div className="border-b border-gray-200 pb-2">
                                    <div className="font-medium text-gray-700 mb-1">Log File Path</div>
                                    <div className="text-xs font-mono text-gray-600 break-words">
                                        {logFilePath || processInfo?.logFilePath || 'N/A'}
                                    </div>
                                </div>
                            )}
                        </div>
                        {connected && (
                            <button
                                className="cursor-pointer w-full text-xs font-medium px-3 py-2 rounded-md bg-red-600 text-white hover:bg-red-700 transition"
                                onClick={handleKillServer}
                            >
                                Shutdown Server
                            </button>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

export default WebsocketStatusMenu;
