// SceneInitializer.tsx
import { useThree } from "@react-three/fiber";
import { useEffect } from "react";
import { useModelStore } from "../../../state/modelStore";

const SceneInitializer = () => {
  const { scene, raycaster, camera } = useThree();
  const { setScene, setRaycaster } = useModelStore();

  useEffect(() => {
    setScene(scene); // 💾 Set the canvas scene once when available
    setRaycaster(raycaster);

    raycaster.params.Line.threshold = 0.01;
    raycaster.params.Points.threshold = 0.01;

    // Set raycaster to only detect objects on layer 0
    raycaster.layers.set(0);
    raycaster.layers.disable(1);

    camera.layers.enable(0);
    camera.layers.enable(1);
  }, [scene]);

  return null;
};

export default SceneInitializer;
