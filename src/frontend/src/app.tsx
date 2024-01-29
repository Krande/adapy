// src/App.js
import "./app.css";
import React from 'react'
import CanvasComponent from './components/viewer/Canvas';
import NavBar from './components/NavBar';
import {useNavBarStore} from './state/navBarStore'; // import the useNavBarStore function

function App() {
    const {isNavBarVisible, setIsNavBarVisible} = useNavBarStore(); // use the useNavBarStore function

    return (
        <div className={"relative flex flex-row h-full w-full bg-gray-900"}>
            <div className={isNavBarVisible ? "w-60" : "w-0 overflow-hidden"}>
                <NavBar setIsNavBarVisible={setIsNavBarVisible}/>
            </div>

            <div className={isNavBarVisible ? "flex-1" : "w-full h-full"}>
                <CanvasComponent/>
            </div>

            {!isNavBarVisible && (
                <button
                    className={"absolute bottom-0 left-0 bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                    onClick={() => setIsNavBarVisible(true)}
                >☰</button>
            )}
        </div>
    );
}

export default App;