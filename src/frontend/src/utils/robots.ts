import {useModelState} from "../state/modelState";
import URDFLoader from "urdf-loader";
import {STLLoader} from "three/examples/jsm/loaders/STLLoader";
import * as THREE from "three";
import {sceneRef} from "../state/refs"; // or your existing import style

export function loadRobot() {
    console.log("Loading robot");
    const safeLoader = new STLLoader();
    safeLoader.parse = function (data) {
        try {
            // Try binary first
            return STLLoader.prototype.parse.call(this, data);
        } catch (err) {
            console.warn("Binary STL failed. Trying ASCII fallback...", err);
            if (typeof data === "string") {
                // Already a string, no need to decode
                return STLLoader.prototype.parse.call(this, data);
            } else {
                // Decode the binary buffer into text
                const textDecoder = new TextDecoder();
                const text = textDecoder.decode(data);
                return STLLoader.prototype.parse.call(this, text);
            }
        }
    };
    let scene = sceneRef.current;

    if (!scene) {
        // Make sure scene is created here
        console.error("Scene is not defined");
    }
    const url = "./models/kr4_r600/urdf/kuka_kr4.urdf";
    const urdfLoader = new URDFLoader();
    urdfLoader.workingPath = "./models/kr4_r600";
    urdfLoader.packages = {
        "meshes": "/models/kr4_r600/meshes"
    };
    urdfLoader.loadMeshCb = (path, manager, onComplete) => {
        fetch(path)
            .then((res) => {
                if (!res.ok) {
                    throw new Error(`HTTP error ${res.status} for ${path}`);
                }
                return res.arrayBuffer();
            })
            .then((data) => {
                const bytes = new Uint8Array(data.slice(0, 100));
                const preview = new TextDecoder().decode(bytes);
                console.log("STL header preview:", preview);

                const geometry = safeLoader.parse(data);
                const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({color: 0x999999}));
                onComplete(mesh);
            })
            .catch((err) => {
                console.error(`Failed to load STL at ${path}`, err);
                onComplete(null, err);
            });
    };
    urdfLoader.load(
        url,
        (robot) => {
            console.log("Robot loaded:", robot);
            if (scene) {
                robot.traverse(c => {
                    c.castShadow = true;
                });
                scene.add(robot);
            } else {
                console.error("Scene still not available at load time");
            }
        },
        (err) => {
            console.error("Failed to load URDF:", err);
        }
    );
}
