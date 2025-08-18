import * as THREE from "three";
import {sceneRef, cameraRef, rendererRef, selectedPointRef} from "../../state/refs";
import {useOptionsStore} from "../../state/optionsStore";
import {createSphericalPointMaterial} from "./pointsImpostor";
import {selectedMaterial} from "../default_materials";

// Configuration for selection display method
const USE_SPHERE_MESH = true; // Set to true to use sphere mesh, false for impostor points

function createSelectedPointSphere(worldPosition: THREE.Vector3, size: number, color: THREE.Color): THREE.Mesh {
    // Calculate sphere radius - make it slightly larger than the point size
    const ps = useOptionsStore.getState().pointSize;
    const sphereRadius = ps * 1.05 // Convert point size to world units with minimum

    // Create sphere geometry with reasonable detail
    const sphereGeometry = new THREE.SphereGeometry(sphereRadius, 16, 12);

    // Create material that responds to lighting
    const sphereMaterial = new THREE.MeshLambertMaterial({
        color: color,
        transparent: false,
        opacity: 1.0,
        depthTest: true,
        depthWrite: true
    });


    // Create and position the sphere mesh
    const sphereMesh = new THREE.Mesh(sphereGeometry, sphereMaterial);
    console.log(`sphere placed at ${worldPosition.x}, ${worldPosition.y}, ${worldPosition.z} with radius ${sphereRadius}`)
    sphereMesh.position.copy(worldPosition);
    sphereMesh.renderOrder = 9999; // Draw on top

    return sphereMesh;
}

function createSelectedPointImpostor(worldPosition: THREE.Vector3, size: number, color: THREE.Color): THREE.Points {
    // Read sizing mode and viewport info
    const opts = useOptionsStore.getState();
    const absolute = !!opts.pointSizeAbsolute;
    const renderer = rendererRef.current;
    const cam = cameraRef.current as THREE.PerspectiveCamera | null;
    const viewportHeight = renderer ? renderer.getSize(new THREE.Vector2()).y : window.innerHeight;
    const fov = cam && (cam as any).isPerspectiveCamera ? cam.fov : 50.0;

    const displaySize = Math.max(size * 1.5, size + 0.01);

    // Create geometry with single point
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.Float32BufferAttribute([worldPosition.x, worldPosition.y, worldPosition.z], 3));

    // Create impostor material
    const mat = createSphericalPointMaterial({
        pointSize: displaySize,
        color: color,
        opacity: 1.0,
        useVertexColors: false,
        depthTest: true,
        depthWrite: true
    });

    // Initialize sizing uniforms for immediate correctness
    if ((mat as any).uniforms) {
        const u = (mat as any).uniforms;
        if (u.pointSize) u.pointSize.value = displaySize;
        if (u.uWorldSize) u.uWorldSize.value = absolute;
        if (u.uWorldPointSize) u.uWorldPointSize.value = displaySize;
        if (u.uFov) u.uFov.value = fov;
        if (u.uViewportHeight) u.uViewportHeight.value = viewportHeight;
        if (u.uColor) u.uColor.value = color;
        (mat as any).needsUpdate = true;
    }

    const points = new THREE.Points(geom, mat);
    points.renderOrder = 9999; // Draw on top

    return points;
}

function updateSelectedPointSphere(sphereMesh: THREE.Mesh, worldPosition: THREE.Vector3, size: number, color: THREE.Color) {
    // Update position
    sphereMesh.position.copy(worldPosition);

    // Update color
    const material = sphereMesh.material as THREE.MeshBasicMaterial;
    material.color = color;
    material.needsUpdate = true;
}

function updateSelectedPointImpostor(points: THREE.Points, worldPosition: THREE.Vector3, size: number, color: THREE.Color) {
    // Update position
    const geom = points.geometry as THREE.BufferGeometry;
    const posAttr = geom.getAttribute('position') as THREE.BufferAttribute;
    if (posAttr && posAttr.count === 1) {
        posAttr.setXYZ(0, worldPosition.x, worldPosition.y, worldPosition.z);
        posAttr.needsUpdate = true;
    } else {
        geom.setAttribute('position', new THREE.Float32BufferAttribute([worldPosition.x, worldPosition.y, worldPosition.z], 3));
    }

    // Update material uniforms
    const opts = useOptionsStore.getState();
    const absolute = !!opts.pointSizeAbsolute;
    const renderer = rendererRef.current;
    const cam = cameraRef.current as THREE.PerspectiveCamera | null;
    const viewportHeight = renderer ? renderer.getSize(new THREE.Vector2()).y : window.innerHeight;
    const fov = cam && (cam as any).isPerspectiveCamera ? cam.fov : 50.0;
    const displaySize = Math.max(size * 1.5, size + 0.01);

    const mat = points.material as THREE.ShaderMaterial & { uniforms?: any };
    if (mat.uniforms) {
        if (mat.uniforms.pointSize) mat.uniforms.pointSize.value = displaySize;
        if (mat.uniforms.uWorldSize) mat.uniforms.uWorldSize.value = absolute;
        if (mat.uniforms.uWorldPointSize) mat.uniforms.uWorldPointSize.value = displaySize;
        if (mat.uniforms.uFov) mat.uniforms.uFov.value = fov;
        if (mat.uniforms.uViewportHeight) mat.uniforms.uViewportHeight.value = viewportHeight;
        if (mat.uniforms.uColor) mat.uniforms.uColor.value = color;
        mat.needsUpdate = true;
    }
}

export function showSelectedPoint(worldPosition: THREE.Vector3, size: number) {
    const scene = sceneRef.current;
    if (!scene) return;

    // Determine highlight color from the shared selected material
    const highlightColor = selectedMaterial.color; // THREE.Color

    // Create or update the singleton highlight object
    let hl = selectedPointRef.current;

    if (!hl) {
        // Create new highlight object based on configuration
        if (USE_SPHERE_MESH) {
            hl = createSelectedPointSphere(worldPosition, size, highlightColor);
        } else {
            hl = createSelectedPointImpostor(worldPosition, size, highlightColor);
        }

        selectedPointRef.current = hl;
        scene.add(hl);
    } else {
        // Update existing highlight object
        if (USE_SPHERE_MESH && hl instanceof THREE.Mesh) {
            updateSelectedPointSphere(hl, worldPosition, size, highlightColor);
        } else if (!USE_SPHERE_MESH && hl instanceof THREE.Points) {
            updateSelectedPointImpostor(hl, worldPosition, size, highlightColor);
        } else {
            // Configuration changed - remove old and create new
            scene.remove(hl);
            if (hl.geometry) hl.geometry.dispose();
            if (Array.isArray(hl.material)) {
                hl.material.forEach(m => m.dispose());
            } else {
                (hl.material as THREE.Material).dispose();
            }

            // Create new with current configuration
            if (USE_SPHERE_MESH) {
                hl = createSelectedPointSphere(worldPosition, size, highlightColor);
            } else {
                hl = createSelectedPointImpostor(worldPosition, size, highlightColor);
            }

            selectedPointRef.current = hl;
            scene.add(hl);
        }
    }
}

export function showSelectedPoints(worldPositions: THREE.Vector3[], size: number) {
    const scene = sceneRef.current;
    if (!scene) return;

    // Always use impostor points for multi-point display for performance and simplicity
    const displaySize = Math.max(size * 1.5, size + 0.01);

    // Build geometry with all positions
    const positions = new Float32Array(worldPositions.length * 3);
    for (let i = 0; i < worldPositions.length; i++) {
        const p = worldPositions[i];
        positions[i * 3 + 0] = p.x;
        positions[i * 3 + 1] = p.y;
        positions[i * 3 + 2] = p.z;
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    const mat = createSphericalPointMaterial({
        pointSize: displaySize,
        color: selectedMaterial.color,
        opacity: 1.0,
        useVertexColors: false,
        depthTest: true,
        depthWrite: true
    });

    // Initialize uniforms
    const opts = useOptionsStore.getState();
    const absolute = !!opts.pointSizeAbsolute;
    const renderer = rendererRef.current;
    const cam = cameraRef.current as THREE.PerspectiveCamera | null;
    const viewportHeight = renderer ? renderer.getSize(new THREE.Vector2()).y : window.innerHeight;
    const fov = cam && (cam as any).isPerspectiveCamera ? cam.fov : 50.0;
    if ((mat as any).uniforms) {
        const u = (mat as any).uniforms;
        if (u.pointSize) u.pointSize.value = displaySize;
        if (u.uWorldSize) u.uWorldSize.value = absolute;
        if (u.uWorldPointSize) u.uWorldPointSize.value = displaySize;
        if (u.uFov) u.uFov.value = fov;
        if (u.uViewportHeight) u.uViewportHeight.value = viewportHeight;
        if (u.uColor) u.uColor.value = selectedMaterial.color;
        (mat as any).needsUpdate = true;
    }

    // Remove previous highlight (single or multi)
    const prev = selectedPointRef.current;
    if (prev) {
        scene.remove(prev);
        if (prev.geometry) prev.geometry.dispose();
        if (Array.isArray(prev.material)) {
            prev.material.forEach(m => m.dispose());
        } else {
            (prev.material as THREE.Material).dispose();
        }
    }

    const points = new THREE.Points(geom, mat);
    points.renderOrder = 9999;
    selectedPointRef.current = points;
    scene.add(points);
}

export function clearSelectedPoint() {
    const scene = sceneRef.current;
    const hl = selectedPointRef.current;
    if (!scene || !hl) return;

    scene.remove(hl);
    if (hl.geometry) hl.geometry.dispose();
    if (Array.isArray(hl.material)) {
        hl.material.forEach(m => m.dispose());
    } else {
        (hl.material as THREE.Material).dispose();
    }
    selectedPointRef.current = null;
}


export type HighlightSource = { pointsObj: THREE.Points, index: number };

function computeDeformedWorldPos(pointsObj: THREE.Points, index: number): THREE.Vector3 | null {
    const geom = pointsObj.geometry as THREE.BufferGeometry;
    const posAttr = geom.getAttribute('position') as THREE.BufferAttribute | undefined;
    if (!posAttr || index < 0 || index >= posAttr.count) return null;
    const local = new THREE.Vector3(
        posAttr.getX(index),
        posAttr.getY(index),
        posAttr.getZ(index)
    );
    const morphs = (geom.morphAttributes && geom.morphAttributes.position) as THREE.BufferAttribute[] | undefined;
    const rel = geom.morphTargetsRelative === true;
    const influences: number[] | undefined = (pointsObj as any).morphTargetInfluences;
    if (morphs && influences && morphs.length === influences.length) {
        let sum = 0;
        for (let i = 0; i < morphs.length; i++) {
            const inf = influences[i] || 0;
            if (inf === 0) continue;
            sum += inf;
            const mp = morphs[i];
            const mx = mp.getX(index), my = mp.getY(index), mz = mp.getZ(index);
            if (rel) {
                local.x += mx * inf;
                local.y += my * inf;
                local.z += mz * inf;
            } else {
                local.x = local.x * (1 - sum) + mx * inf;
                local.y = local.y * (1 - sum) + my * inf;
                local.z = local.z * (1 - sum) + mz * inf;
            }
        }
    }
    return local.applyMatrix4(pointsObj.matrixWorld);
}

export function showSelectedPointsFromSources(sources: HighlightSource[], size: number) {
    const scene = sceneRef.current;
    if (!scene) return;

    if (!sources || sources.length === 0) {
        clearSelectedPoint();
        return;
    }

    // Compute initial positions
    const worldPositions: THREE.Vector3[] = [];
    for (const s of sources) {
        const wp = computeDeformedWorldPos(s.pointsObj, s.index);
        if (wp) worldPositions.push(wp);
    }
    if (worldPositions.length === 0) {
        clearSelectedPoint();
        return;
    }

    const displaySize = Math.max(size * 1.5, size + 0.01);

    const positions = new Float32Array(worldPositions.length * 3);
    for (let i = 0; i < worldPositions.length; i++) {
        const p = worldPositions[i];
        positions[i * 3 + 0] = p.x;
        positions[i * 3 + 1] = p.y;
        positions[i * 3 + 2] = p.z;
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    const mat = createSphericalPointMaterial({
        pointSize: displaySize,
        color: selectedMaterial.color,
        opacity: 1.0,
        useVertexColors: false,
        depthTest: true,
        depthWrite: true
    });

    // Initialize uniforms
    const opts = useOptionsStore.getState();
    const absolute = !!opts.pointSizeAbsolute;
    const renderer = rendererRef.current;
    const cam = cameraRef.current as THREE.PerspectiveCamera | null;
    const viewportHeight = renderer ? renderer.getSize(new THREE.Vector2()).y : window.innerHeight;
    const fov = cam && (cam as any).isPerspectiveCamera ? cam.fov : 50.0;
    if ((mat as any).uniforms) {
        const u = (mat as any).uniforms;
        if (u.pointSize) u.pointSize.value = displaySize;
        if (u.uWorldSize) u.uWorldSize.value = absolute;
        if (u.uWorldPointSize) u.uWorldPointSize.value = displaySize;
        if (u.uFov) u.uFov.value = fov;
        if (u.uViewportHeight) u.uViewportHeight.value = viewportHeight;
        if (u.uColor) u.uColor.value = selectedMaterial.color;
        (mat as any).needsUpdate = true;
    }

    // Remove previous highlight
    const prev = selectedPointRef.current;
    if (prev) {
        scene.remove(prev);
        if (prev.geometry) prev.geometry.dispose();
        if (Array.isArray(prev.material)) {
            prev.material.forEach(m => m.dispose());
        } else {
            (prev.material as THREE.Material).dispose();
        }
    }

    const points = new THREE.Points(geom, mat);
    points.renderOrder = 9999;
    // Store sources for updates
    (points as any).userData._deformSources = sources;

    // Per-frame updater to track morph deformation
    points.onBeforeRender = () => {
        const srcs: HighlightSource[] | undefined = (points as any).userData._deformSources;
        if (!srcs) return;
        const pos = (points.geometry as THREE.BufferGeometry).getAttribute('position') as THREE.BufferAttribute;
        if (!pos) return;
        const arr = pos.array as Float32Array;
        let w = 0;
        for (let i = 0; i < srcs.length; i++) {
            const s = srcs[i];
            const wp = computeDeformedWorldPos(s.pointsObj, s.index);
            if (wp) {
                arr[w++] = wp.x;
                arr[w++] = wp.y;
                arr[w++] = wp.z;
            } else {
                // Keep previous if not found
                w += 3;
            }
        }
        pos.needsUpdate = true;
    };

    selectedPointRef.current = points;
    scene.add(points);
}
