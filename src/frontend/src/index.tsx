import {createRoot} from 'react-dom/client';
import App from './app';
import React from "react";

const container = document.getElementById('root');
// @ts-ignore
const root = createRoot(container); // create a root
root.render(<App/>);