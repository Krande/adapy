import {useModelStore} from "../state/modelStore";
import URDFLoader, {URDFRobot} from "urdf-loader";

export function loadRobot() {
    console.log("Loading robot");
    let scene = useModelStore.getState().scene

    if (!scene) {
        // Make sure scene is created here
        console.error("Scene is not defined");
    }
    const url = './models/robot1/urdf/kuka_kr4.urdf';
    const urdfLoader = new URDFLoader();
    urdfLoader.workingPath = './models/robot1'
    urdfLoader.load(url, robot => {
        if (!scene) {
            throw new Error("Scene is not defined");
        }
        console.log("Robot loaded:", robot);
        // The robot is loaded!
        scene.add(robot);
    },(onError) => {
        console.error("Error loading robot:", onError);
    }, (onProgress) => {
        console.log("Loading robot progress:", onProgress);
    }
    );
}

