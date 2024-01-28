import React from 'react';
import {useAnimationStore} from "../state/animationStore";
import {useNavBarStore} from "../state/navBarStore";
import {useWebSocketStore} from '../state/webSocketStore';

type NavBarProps = {
    setIsNavBarVisible: (value: boolean) => void;
};

const NavBar: React.FC<NavBarProps> = ({setIsNavBarVisible}) => {
    const {showPerf, setShowPerf} = useNavBarStore(); // use showPerf and setShowPerf from useNavBarStore
    const {sendData} = useWebSocketStore();

    return (
        <div className={"flex flex-col space-y-4 p-2"}>

            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={() => sendData('Hello from React')}
            >
                Send Message
            </button>
            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={() => console.log(useAnimationStore.getState())}
            >Print State
            </button>
            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={() => setShowPerf(!showPerf)}
            >Show stats
            </button>
            <button
                className={"absolute bottom-0 left-0 bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                onClick={() => setIsNavBarVisible(false)}
            >
                ☰
            </button>
        </div>
    );
}

export default NavBar;