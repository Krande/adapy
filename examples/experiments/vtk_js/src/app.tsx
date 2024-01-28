// src/App.js
import "./app.css";
import React from 'react'
import VTKRenderer from "./components/VTKRenderer";

function App() {

    return (
        <div className={"flex flex-col h-full w-full"}>
            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
            >
                Test
            </button>
            <div className={"flex-1 border-2 border-black"}>
                <VTKRenderer/>
            </div>
        </div>


    );
}

export default App;