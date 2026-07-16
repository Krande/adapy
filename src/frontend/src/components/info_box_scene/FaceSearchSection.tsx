import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import * as THREE from "three";
import {useOptionsStore} from "@/state/optionsStore";
import {sceneRef, cameraRef, controlsRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {queryAllFaceRanges} from "@/utils/mesh_select/queryMeshDrawRange";
import {setFaceHighlight} from "@/utils/mesh_select/faceHighlight";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {frameBox} from "@/components/viewer/sceneHelpers/setupCameraControlsHandlers";

// Search the loaded model's per-face regions (face_ranges) by source face id and jump to a hit:
// highlight that face and frame the camera on it. Only rendered when the model carries face regions
// (same gate as the Solid/Faces toggle). Built to pinpoint a specific mis-tessellated face (e.g. a
// health-report "suspect" id) without hunting for it by eye.

type FaceEntry = {faceId: number; seq: number; start: number; length: number; uniqueKey: string};

function collectMeshes(): CustomBatchedMesh[] {
    const scene = sceneRef.current;
    const out: CustomBatchedMesh[] = [];
    if (!scene) return out;
    scene.traverse((o) => {
        if (o instanceof CustomBatchedMesh) out.push(o);
    });
    return out;
}

// All face regions across every loaded mesh. queryAllFaceRanges returns ABSOLUTE index ranges keyed by
// the mesh-name the metadata cache actually used — try mesh.name then node<node_id>, the same fallback
// handleClickMesh uses.
async function buildIndex(): Promise<FaceEntry[]> {
    const entries: FaceEntry[] = [];
    for (const mesh of collectMeshes()) {
        const names = [mesh.name];
        const nodeId = (mesh.userData as any)?.node_id;
        if (nodeId != null) names.push(`node${nodeId}`);
        for (const nm of names) {
            const got = await queryAllFaceRanges(mesh.unique_key, nm);
            if (got && got.length) {
                for (const f of got)
                    entries.push({faceId: f.faceId, seq: f.seq, start: f.start, length: f.length, uniqueKey: mesh.unique_key});
                break;
            }
        }
    }
    return entries;
}

function meshByKey(key: string): CustomBatchedMesh | null {
    for (const m of collectMeshes()) if (m.unique_key === key) return m;
    return null;
}

// World-space bounding box of a face's triangles (absolute index range) for the camera fit.
function faceWorldBox(mesh: CustomBatchedMesh, start: number, length: number): THREE.Box3 {
    const box = new THREE.Box3();
    const geom = mesh.geometry as THREE.BufferGeometry;
    const index = geom.getIndex();
    const pos = geom.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!index || !pos) return box;
    const end = Math.min(start + length, index.count);
    const v = new THREE.Vector3();
    for (let i = start; i < end; i++) {
        const vi = index.getX(i);
        v.set(pos.getX(vi), pos.getY(vi), pos.getZ(vi));
        box.expandByPoint(v);
    }
    mesh.updateWorldMatrix(true, false);
    box.applyMatrix4(mesh.matrixWorld);
    return box;
}

const FaceSearchSection: React.FC = () => {
    const available = useOptionsStore((s) => s.faceRegionsAvailable);
    const setFaces = useOptionsStore((s) => s.setFaceLevelPicking);
    const [query, setQuery] = useState("");
    const [entries, setEntries] = useState<FaceEntry[] | null>(null);
    const [loading, setLoading] = useState(false);
    const buildingRef = useRef(false);

    // Drop the cached index whenever face availability flips (a new model loaded) so a stale index from
    // the previous model can't return ids that no longer exist.
    useEffect(() => {
        setEntries(null);
        setQuery("");
    }, [available]);

    const ensureIndex = useCallback(async () => {
        if (buildingRef.current || entries) return;
        buildingRef.current = true;
        setLoading(true);
        try {
            const idx = await buildIndex();
            idx.sort((a, b) => a.faceId - b.faceId || a.seq - b.seq);
            setEntries(idx);
        } finally {
            setLoading(false);
            buildingRef.current = false;
        }
    }, [entries]);

    const results = useMemo(() => {
        const q = query.trim();
        if (!entries || !q) return [];
        return entries.filter((e) => String(e.faceId).includes(q)).slice(0, 50);
    }, [entries, query]);

    const selectFace = (e: FaceEntry) => {
        const mesh = meshByKey(e.uniqueKey);
        if (!mesh) return;
        setFaces(true); // ensure Faces mode so the per-face highlight is visible
        setFaceHighlight(mesh, e.start, e.length);
        useObjectInfoStore.getState().setClickedFace({faceId: e.faceId, seq: e.seq});
        const box = faceWorldBox(mesh, e.start, e.length);
        const c = cameraRef.current, ctl = controlsRef.current;
        if (c && ctl && !box.isEmpty()) frameBox(box, c, ctl);
        requestRender();
    };

    if (!available) return null;

    return (
        <div className="py-1">
            <input
                type="text"
                value={query}
                onFocus={() => void ensureIndex()}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={loading ? "indexing faces…" : "Search face id…"}
                className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded-sm text-gray-100 placeholder-gray-500"
                aria-label="Search faces by id"
            />
            {query.trim() && entries && (
                <div className="mt-1 max-h-40 overflow-auto rounded-sm border border-gray-700">
                    {results.length === 0 ? (
                        <div className="px-2 py-1 text-xs text-gray-400">no matching face</div>
                    ) : (
                        results.map((e) => (
                            <button
                                key={`${e.uniqueKey}:${e.faceId}:${e.seq}`}
                                type="button"
                                onClick={() => selectFace(e)}
                                className="block w-full text-left px-2 py-0.5 text-xs text-gray-200 hover:bg-blue-700"
                            >
                                Face #{e.faceId} <span className="text-gray-400">(seq {e.seq})</span>
                            </button>
                        ))
                    )}
                </div>
            )}
        </div>
    );
};

export default FaceSearchSection;
