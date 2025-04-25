import { useModelStore } from "../state/modelStore";
import URDFLoader from "urdf-loader";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader";
import * as THREE from "three"; // or your existing import style

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
  let scene = useModelStore.getState().scene;

  if (!scene) {
    // Make sure scene is created here
    console.error("Scene is not defined");
  }
  const url = "./models/kr4_r600/urdf/kuka_kr4.urdf";
  const urdfLoader = new URDFLoader();
  urdfLoader.workingPath = "./models/kr4_r600";
  // urdfLoader.packages = {
  //   packageName: "./models/kr4_r600",
  // };
  urdfLoader.loadMeshCb = (path, manager, done) => {
    fetch(path)
      .then((res) => res.arrayBuffer())
      .then((data) => {
        const geometry = safeLoader.parse(data);

        // Default gray material
        let material = new THREE.MeshStandardMaterial({ color: 0x999999 });
        const mesh = new THREE.Mesh(geometry, material);
        done(mesh);
      });
  };
}
