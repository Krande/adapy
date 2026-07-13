import React from "react";
import {useOptionsStore} from "@/state/optionsStore";

// Solid/Faces picking mode. Only rendered when the loaded model carries per-face regions
// (face_ranges extras). "Faces" routes clicks through the raycast path so the exact source face
// (STEP/IFC #id) resolves and shows in the object Properties panel — useful for pinpointing a
// mis-tessellated face. "Solid" keeps the fast GPU object-pick.
const FacePickingToggle: React.FC = () => {
    const available = useOptionsStore((s) => s.faceRegionsAvailable);
    const faces = useOptionsStore((s) => s.faceLevelPicking);
    const setFaces = useOptionsStore((s) => s.setFaceLevelPicking);
    if (!available) return null;

    const btn = (active: boolean) =>
        "flex-1 px-2 py-0.5 text-xs rounded-sm transition-colors " +
        (active ? "bg-blue-700 text-white" : "bg-gray-700 text-gray-200 hover:bg-gray-600");

    return (
        <div className="flex items-center gap-2 py-1">
            <span className="text-xs text-gray-300">Click picks:</span>
            <div className="flex gap-1 flex-1" role="group" aria-label="Click picking mode">
                <button type="button" className={btn(!faces)} onClick={() => setFaces(false)} aria-pressed={!faces}>
                    Solid
                </button>
                <button type="button" className={btn(faces)} onClick={() => setFaces(true)} aria-pressed={faces}>
                    Faces
                </button>
            </div>
        </div>
    );
};

export default FacePickingToggle;
