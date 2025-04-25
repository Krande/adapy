import { useModelStore } from "../state/modelStore";
import URDFLoader from "urdf-loader";

export function loadRobot() {
  console.log("Loading robot");
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
  urdfLoader.load(
    url,
    (robot) => {
      if (!scene) {
        throw new Error("Scene is not defined");
      }
      console.log("Robot loaded:", robot);
      // The robot is loaded!
      scene.add(robot);
    },
    (onError) => {
      console.error("Error loading robot:", onError);
    },
    (onProgress) => {
      console.log("Loading robot progress:", onProgress);
    },
  );
}
