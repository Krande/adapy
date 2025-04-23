import {useModelStore} from "../state/modelStore";
import URDFLoader, { URDFRobot } from "urdf-loader";
import {XacroLoader} from 'xacro-parser';

export function loadRobot() {
    console.log("Loading robot");
    let scene = useModelStore.getState().scene

    if (!scene) {
        // Make sure scene is created here
        console.error("Scene is not defined");
    }
    const url = './models/robot1/robot1.xacro';
    const xacroLoader = new XacroLoader();
    const urdfLoader = new URDFLoader();

    xacroLoader.load(
        url,
        (xml) => {
            try {
                const robot: URDFRobot = urdfLoader.parse(xml);
                if (!robot) {
                    throw new Error("Failed to parse URDF");
                }
                if (!scene) {
                    throw new Error("Scene is not defined");
                }
                scene.add(robot);
                console.log("Parsed URDF Robot:", robot);
                // You can now add robot to your Three.js scene, etc.
            } catch (err) {
                console.error("Error parsing URDF:", err);
            }
        },
        (err) => {
            console.error("Error loading:", err);
        }
    );
}

