import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {useOptionsStore} from "@/state/optionsStore";
import {useViewerRefs} from "@/state/AdaViewerContext";
import {findFaces, focusFace, type FaceEntry} from "@/utils/mesh_select/faceSearch";

// Search the loaded model's per-face regions (face_ranges) by source face id and jump to a hit:
// highlight that face and frame the camera on it. Only rendered when the model carries face regions
// (same gate as the Solid/Faces toggle). Built to pinpoint a specific mis-tessellated face (e.g. a
// health-report "suspect" id) without hunting for it by eye. The lookup and the focus are
// utils/mesh_select/faceSearch, which plugins reach as the `findFaces` / `focusFace` scene verbs.

const FaceSearchSection: React.FC = () => {
    // This viewer instance's handles, to frame the face in this viewer.
    const {camera, controls} = useViewerRefs();
    const available = useOptionsStore((s) => s.faceRegionsAvailable);
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
            setEntries(await findFaces());
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
        focusFace(e, {view: {camera, controls}});
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
                                key={`${e.model}:${e.faceId}:${e.seq}`}
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
