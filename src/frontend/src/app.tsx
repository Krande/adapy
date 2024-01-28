// src/App.js
import "./app.css";
import React, {useState} from 'react'
import CanvasComponent from './components/viewer/Canvas';
import NavBar from './components/NavBar';

function App() {
    const [isNavBarVisible, setIsNavBarVisible] = useState(true);

    return (
        <div className={"flex flex-row h-full w-full"}>
            <NavBar/>
            {/*{isNavBarVisible && <NavBar setIsNavBarVisible={setIsNavBarVisible}/>}*/}
            <div className={isNavBarVisible ? "flex-1" : "flex-1 w-full h-full"}>
                <CanvasComponent/>
            </div>
        </div>
    );
}

export default App;