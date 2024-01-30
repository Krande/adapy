import {createRoot} from 'react-dom/client';
import App from './app';
import React from "react";
import {webSocketHandler} from "./utils/websocket_connector";
import {useWebSocketStore} from "./state/webSocketStore";

// start websocket here
const url = useWebSocketStore.getState().webSocketAddress;
webSocketHandler.connect(url);

const container = document.getElementById('root');
// @ts-ignore
const root = createRoot(container); // create a root
root.render(<App/>);