// src/App.js
import "./app.css";
import React from 'react'
import VTKRenderer from "./components/VTKRenderer";

function App() {

    return (
        <div className={"flex flex-col h-full w-full"}>
            <VTKRenderer/>
        </div>


    );
}

export default App;