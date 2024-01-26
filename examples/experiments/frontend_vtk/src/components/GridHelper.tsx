import * as THREE from 'three';
import {useThree} from '@react-three/fiber';
import {useEffect} from "react";

function GridHelper({size = 10, divisions = 10, colorCenterLine = 'gray', colorGrid = 'gray'}) {
    const {scene} = useThree();

    useEffect(() => {
        const gridHelper = new THREE.GridHelper(size, divisions, colorCenterLine, colorGrid);
        scene.add(gridHelper);
        return () => {
            scene.remove(gridHelper);
        };
    }, [scene, size, divisions, colorCenterLine, colorGrid]);

    return null;
}

export default GridHelper;
