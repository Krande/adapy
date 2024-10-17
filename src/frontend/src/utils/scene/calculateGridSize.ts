// calculateGridSize.ts
import * as THREE from 'three';

export function calculateGridSize(boundingBox: THREE.Box3): number {
  const size = new THREE.Vector3();
  boundingBox.getSize(size);

  // Calculate the maximum dimension
  const maxDimension = Math.max(size.x, size.y, size.z);

  // Increase the size by 50%
  const gridSize = maxDimension * 1.5;

  // Round up to the nearest integer for grid divisions
  return Math.ceil(gridSize);
}
