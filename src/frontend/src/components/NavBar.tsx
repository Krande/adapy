import React from 'react';
import {useAnimationStore} from "../state/animationStore";
import {useNavBarStore} from "../state/navBarStore";
import {useColorStore} from "../state/colorLegendStore";


type NavBarProps = {
    setIsNavBarVisible: (value: boolean) => void;
    sendMessage: (message: string | object) => void;
};

const NavBar: React.FC<NavBarProps> = ({setIsNavBarVisible, sendMessage}) => {
    const {showPerf, setShowPerf} = useNavBarStore(); // use showPerf and setShowPerf from useNavBarStore
    const {showLegend, setShowLegend} = useColorStore();


    return (
        <div className={"flex flex-col space-y-4 p-2"}>

            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={() => sendMessage('Hello from React')}
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
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={() => setShowLegend(!showLegend)}
            >Show ColorLegend
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