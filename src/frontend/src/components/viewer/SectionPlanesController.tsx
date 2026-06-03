import React from "react";
import * as THREE from "three";
import {TransformControls} from "three/examples/jsm/controls/TransformControls";

import {sceneRef, rendererRef, cameraRef, controlsRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useSectionStore} from "@/state/sectionStore";
import {useModelState} from "@/state/modelState";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {gpuMeshPicker} from "@/utils/mesh_select/GpuMeshPicker";
import {createPlaneStencilGroup, createCapMesh, orientCapToPlane} from "@/utils/scene/section_caps";

// Headless: reconciles the section-plane store with three.js (per-material
// clipping + stencil caps + a drag gizmo). Renders nothing.
const SectionPlanesController: React.FC = () => {
    React.useEffect(() => {
        let cleanup: (() => void) | null = null;
        let raf = 0;

        const tryInit = () => {
            const renderer = rendererRef.current;
            const scene = sceneRef.current;
            const camera = cameraRef.current;
            if (!renderer || !scene || !camera) {
                raf = requestAnimationFrame(tryInit);  // wait for ThreeCanvas to set refs
                return;
            }
            cleanup = init(renderer, scene, camera);
        };
        tryInit();

        return () => {
            cancelAnimationFrame(raf);
            cleanup?.();
        };
    }, []);

    return null;
};

function init(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.Camera,
): () => void {
    {
        // NOTE: do NOT enable localClippingEnabled or touch materials until a
        // plane actually exists — doing it eagerly forces a full shader
        // recompile of the whole model on load (a real regression on big models).
        let clippingApplied = false;

        const container = new THREE.Group();
        container.name = "__section_planes__";
        container.userData.__excludeFromFit = true;  // keep caps/stencil out of zoom-to-all
        scene.add(container);

        // Live THREE.Plane per store-plane id; the SAME instances are shared by
        // the geometry materials, stencil groups, caps and helpers, so mutating
        // a plane's constant during a gizmo drag updates everything at once.
        const planeById = new Map<string, THREE.Plane>();
        const capById = new Map<string, THREE.Mesh>();

        // Gizmo: drags a handle along the active plane's normal.
        const handle = new THREE.Object3D();
        handle.userData.__excludeFromFit = true;
        scene.add(handle);
        const gizmo = new TransformControls(camera, renderer.domElement);
        gizmo.setMode("translate");
        gizmo.setSpace("world");
        const gizmoHelper = gizmo.getHelper();
        gizmoHelper.visible = false;
        gizmoHelper.userData.__excludeFromFit = true;  // gizmo scales w/ distance — never fit to it
        scene.add(gizmoHelper);

        gizmo.addEventListener("dragging-changed", (e: any) => {
            if (controlsRef.current) controlsRef.current.enabled = !e.value;
            if (!e.value) {
                // Commit final position to the store (triggers a clean rebuild).
                const id = useSectionStore.getState().activeId;
                const sp = useSectionStore.getState().planes.find((p) => p.id === id);
                if (id && sp) {
                    const n = new THREE.Vector3(...sp.normal);
                    useSectionStore.getState().setConstant(id, -n.dot(handle.position));
                }
            }
            requestRender();
        });
        gizmo.addEventListener("objectChange", () => {
            const id = useSectionStore.getState().activeId;
            if (!id) return;
            const plane = planeById.get(id);
            const sp = useSectionStore.getState().planes.find((p) => p.id === id);
            const cap = capById.get(id);
            if (!plane || !sp) return;
            // Live update without a store write (no rebuild mid-drag).
            const n = new THREE.Vector3(...sp.normal);
            plane.constant = -n.dot(handle.position);
            if (cap) orientCapToPlane(cap, plane);
            requestRender();
        });

        const isEffectivelyVisible = (o: THREE.Object3D): boolean => {
            let cur: THREE.Object3D | null = o;
            while (cur) {
                if (!cur.visible) return false;
                cur = cur.parent;
            }
            return true;
        };

        // Only visible meshes feed the stencil caps — a hidden model (e.g. the
        // original while "flipped" to a compared build) must not contribute
        // cross-section fill.
        const batchedMeshes = (): CustomBatchedMesh[] => {
            const meshes: CustomBatchedMesh[] = [];
            scene.traverse((o) => {
                if (o instanceof CustomBatchedMesh && isEffectivelyVisible(o)) meshes.push(o);
            });
            return meshes;
        };

        const applyMaterialClipping = (planes: THREE.Plane[]) => {
            // No planes and nothing previously clipped → skip the (expensive)
            // material walk + recompile entirely. This keeps model load fast.
            if (planes.length === 0 && !clippingApplied) return;
            if (planes.length > 0) renderer.localClippingEnabled = true;
            clippingApplied = planes.length > 0;
            const cp = planes.length ? planes : null;
            // Keep the GPU picker's pick render clipped the same way, so clicks
            // on cut-exposed interior elements hit the visible element instead
            // of the (invisible) cut-away shell in front of it.
            gpuMeshPicker.setClippingPlanes(planes);
            scene.traverse((o) => {
                if (o instanceof CustomBatchedMesh) {
                    const mats = Array.isArray(o.material) ? o.material : [o.material];
                    for (const m of mats) {
                        (m as THREE.Material).clippingPlanes = cp;
                        (m as THREE.Material).clipShadows = true;
                        (m as THREE.Material).needsUpdate = true;
                    }
                } else if ((o as THREE.LineSegments).isLineSegments) {
                    // Edge overlay (custom ShaderMaterial w/ clipping:true) — cut it too.
                    const mat: any = (o as THREE.LineSegments).material;
                    if (mat?.uniforms?.uVisibleTex) {
                        mat.clippingPlanes = cp;
                        mat.needsUpdate = true;
                    }
                }
            });
        };

        const disposeContainer = () => {
            for (let i = container.children.length - 1; i >= 0; i--) {
                const o = container.children[i];
                o.traverse((m: any) => {
                    if (m.userData?.__sectionCap && m.geometry?.dispose) m.geometry.dispose();
                    if (m.material) {
                        const mm = m.material;
                        (Array.isArray(mm) ? mm : [mm]).forEach((x: any) => x.dispose?.());
                    }
                });
                container.remove(o);
            }
            planeById.clear();
            capById.clear();
        };

        const rebuild = () => {
            if (!sceneRef.current) return;
            disposeContainer();

            const st = useSectionStore.getState();
            const enabled = st.planes.filter((p) => p.enabled);
            const planes = enabled.map((p) => {
                const pl = new THREE.Plane(new THREE.Vector3(...p.normal).normalize(), p.constant);
                planeById.set(p.id, pl);
                return pl;
            });

            applyMaterialClipping(planes);

            const bb = useModelState.getState().boundingBox;
            const size = bb ? bb.getSize(new THREE.Vector3()).length() * 1.1 : 50;
            const capColor = new THREE.Color(st.capColor);
            const meshes = planes.length ? batchedMeshes() : [];

            enabled.forEach((sp, i) => {
                const plane = planeById.get(sp.id)!;
                const order = i * 2 + 1;

                const helper = new THREE.PlaneHelper(plane, size, 0x2266ff);
                helper.layers.set(1);
                helper.visible = st.gizmoVisible;  // hide plane outline with the gizmo
                container.add(helper);

                for (const m of meshes) {
                    m.updateWorldMatrix(true, false);
                    container.add(createPlaneStencilGroup(m.geometry, plane, order, m.matrixWorld));
                }

                const others = planes.filter((pl) => pl !== plane);
                const cap = createCapMesh(plane, size, capColor, others, order + 1);
                cap.userData.__sectionCap = true;
                capById.set(sp.id, cap);
                container.add(cap);
            });

            // Gizmo follows the active (and enabled) plane — unless hidden, so
            // the user can navigate without grabbing it.
            const active = enabled.find((p) => p.id === st.activeId);
            const activePlane = active ? planeById.get(active.id) : undefined;
            if (active && activePlane && st.gizmoVisible) {
                handle.position.copy(activePlane.coplanarPoint(new THREE.Vector3()));
                gizmo.showX = Math.abs(active.normal[0]) > 0.5;
                gizmo.showY = Math.abs(active.normal[1]) > 0.5;
                gizmo.showZ = Math.abs(active.normal[2]) > 0.5;
                gizmo.attach(handle);
                gizmo.enabled = true;
                gizmoHelper.visible = true;
            } else {
                gizmo.detach();
                gizmo.enabled = false;        // ignore pointer when hidden/none
                gizmoHelper.visible = false;
            }
            requestRender();
        };

        rebuild();
        const unsubSection = useSectionStore.subscribe(rebuild);
        // New / removed / flipped model -> the CustomBatchedMesh set changed;
        // re-apply clipping/caps so the planes follow the current geometry.
        const unsubModel = useModelState.subscribe((s, prev) => {
            if (s.boundingBox !== prev.boundingBox || s.loadedSourceNames !== prev.loadedSourceNames) {
                rebuild();
            }
        });

        return () => {
            unsubSection();
            unsubModel();
            gizmo.detach();
            gizmo.dispose();
            scene.remove(gizmoHelper);
            scene.remove(handle);
            disposeContainer();
            scene.remove(container);
            applyMaterialClipping([]);  // restore unclipped materials
            requestRender();
        };
    }
}

export default SectionPlanesController;
