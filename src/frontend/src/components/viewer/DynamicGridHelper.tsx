// DynamicGridHelper.tsx
import React, { useMemo } from 'react';
import { GridHelper as ThreeGridHelper } from 'three';
import { useThree } from '@react-three/fiber';
import { useModelStore } from '../../state/modelStore';
import { calculateGridSize } from '../../utils/calculateGridSize';

const DynamicGridHelper: React.FC = () => {
  const { boundingBox } = useModelStore();

  const gridHelper = useMemo(() => {
    let grid: ThreeGridHelper;

    if (boundingBox) {
      const size = calculateGridSize(boundingBox);
      const divisions = size;
      grid = new ThreeGridHelper(size, divisions, 'white', 'gray');
      //grid.position.y = boundingBox.min.y - (size * 0.05); // Adjust grid position if needed
    } else {
      // Default grid if no bounding box is available
      grid = new ThreeGridHelper(10, 10, 'white', 'gray');
      grid.position.y = 0;
    }

    return grid;
  }, [boundingBox]);

  return <primitive object={gridHelper} />;
};

export default DynamicGridHelper;
