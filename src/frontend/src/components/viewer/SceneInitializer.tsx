// SceneInitializer.tsx
import { useThree } from '@react-three/fiber';
import { useEffect } from 'react';
import { useModelStore } from '../../state/modelStore';

const SceneInitializer = () => {
    const { scene } = useThree();
    const { setScene } = useModelStore();

    useEffect(() => {
        setScene(scene); // 💾 Set the canvas scene once when available
    }, [scene]);

    return null;
};

export default SceneInitializer;
