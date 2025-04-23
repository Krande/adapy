import {useModelStore} from "../state/modelStore";
import URDFLoader from "urdf-loader";
import { XacroLoader } from 'xacro-parser';
import {LoaderUtils} from "three";

export function loadRobot() {
    console.log("Loading robot");
    let scene = useModelStore.getState().scene

    if (!scene) {
        // Make sure scene is created here

    }
    const url = './models/robot1/robot1.xacro';
    const xacroLoader = new XacroLoader();
    // @ts-ignore
    xacroLoader.load( url, (xml) => {
        const urdfLoader = new URDFLoader();
        urdfLoader.workingPath = LoaderUtils.extractUrlBase( url );

        const robot = urdfLoader.parse( xml );
        if (!scene) {
            console.error('Scene is not defined');
            return;
        }
        console.log("robot", robot);
        scene.add( robot );

    } );
}

