import React from 'react';
import {useMeshHandlers} from "../hooks/useMeshHandlers";


const NavBar = () => {
    const {sendData} = useMeshHandlers();

    return (
        <div className={"flex"}>
            {/*<button*/}
            {/*    className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}*/}
            {/*    // onClick={() => setIsNavBarVisible(false)}*/}
            {/*>*/}
            {/*    ☰*/}
            {/*</button>*/}
            <button
                className={"bg-amber-50 hover:bg-amber-50/50 text-black font-bold py-2 px-4 ml-2 rounded"}
                onClick={() => sendData('Hello from React')}
            >
                Send Message
            </button>
        </div>
    );
}

export default NavBar;