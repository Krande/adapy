import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
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
    const [editingId, setEditingId] = useState(false);
    const [tempId, setTempId] = useState<string>('');
    const inputRef = useRef<HTMLInputElement | null>(null);
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

    useEffect(() => {
        if (editingId && inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
        }
    }, [editingId]);

    const currentId = frontendId || webSocketAsyncHandler.instance_id;

    const startEdit = useCallback(() => {
        setTempId(String(currentId ?? ''));
        setEditingId(true);
    }, [currentId]);

    const cancelEdit = useCallback(() => {
        setEditingId(false);
    }, []);

    const saveEdit = useCallback(async () => {
        const parsed = Number(tempId);
        if (!Number.isFinite(parsed)) {
            alert('Please enter a valid number for Instance ID.');
            return;
        }
        try {
            await webSocketAsyncHandler.setInstanceId(Math.trunc(parsed), true);
            // Server info will refresh after reconnect in handler.connect()
            setEditingId(false);
        } catch (e: any) {
            alert(e?.message || 'Failed to set instance ID');
        }
    }, [tempId]);

    const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveEdit();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            cancelEdit();
        }
    }, [saveEdit, cancelEdit]);

    return (
        <div className="bg-gray-400 bg-opacity-50 rounded p-2 min-w-80 pointer-events-auto">
            <h2 className="font-bold">WebSocket Status</h2>
            <div className="text-xs text-gray-800 space-y-2 mb-2">
                <div className="border-b border-gray-300 pb-2">
                    <div className="font-medium mb-1">Frontend Instance</div>
                    <div className="flex items-center justify-between gap-2">
                        {!editingId ? (
                            <>
                                <span className="font-mono text-xs truncate flex-1" title={String(currentId)}>
                                    ID: {currentId}
                                </span>
                                <button
                                    className="cursor-pointer text-white bg-blue-600 hover:bg-blue-700 text-xs px-2 py-1 rounded"
                                    title="Edit Instance ID"
                                    onClick={startEdit}
                                >
                                    Edit
                                </button>
                            </>
                        ) : (
                            <div className="flex items-center gap-2 flex-1">
                                <input
                                    ref={inputRef}
                                    className="flex-1 text-xs font-mono px-2 py-1 border rounded outline-none focus:ring-2 focus:ring-blue-300"
                                    value={tempId}
                                    onChange={(e) => setTempId(e.target.value)}
                                    onKeyDown={onKeyDown}
                                    aria-label="Frontend Instance ID"
                                />
                                <button
                                    className="cursor-pointer text-white bg-blue-600 hover:bg-blue-700 text-xs px-2 py-1 rounded"
                                    onClick={saveEdit}
                                    title="Save"
                                >
                                    Save
                                </button>
                                <button
                                    className="cursor-pointer text-gray-700 hover:text-gray-900 text-xs px-2 py-1 border border-gray-300 rounded"
                                    onClick={cancelEdit}
                                    title="Cancel"
                                >
                                    Cancel
                                </button>
                            </div>
                        )}
                    </div>
                </div>

                <div className="border-b border-gray-300 pb-2">
                    <div className="font-medium mb-1">WebSocket Server</div>
                    <div className="flex justify-between">
                        <span className="font-medium">Status:</span>
                        <span className={connected ? 'text-green-500 font-semibold' : 'text-red-400 font-semibold'}>
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
