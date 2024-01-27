// src/App.js
import "./app.css";
import React from 'react'
import VTKRenderer from "./components/VTKRenderer";

function App() {

    return (
        <div className={"flex flex-col h-full w-full"}>
            <div className={"flex-1 border-2 border-black"}>
                <VTKRenderer/>
            </div>
        </div>


    );
}

export default App;