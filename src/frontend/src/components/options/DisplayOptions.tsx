import React from "react";
import {Switch} from "@/components/ui";
import {useOptionsStore} from "@/state/optionsStore";
import {useColorStore} from "@/state/colorLegendStore";
import {useModelState} from "@/state/modelState";
import {useSectionStore} from "@/state/sectionStore";
import {refreshEdgeOverlays} from "@/utils/scene/refreshEdgeOverlays";

// Re-chromed onto the design system. Every control drives exactly the setter it drove
// before — only the markup changed.
//
// Switch rather than Checkbox throughout: these all take effect immediately, which is the
// distinction the two primitives encode (a checkbox is a value in a set, or a setting
// that applies on save). The hand-rolled `Toggle` this replaced was a bare
// <input type=checkbox>, which read as "this will apply later".

const DisplayOptions: React.FC = () => {
    const {
        showEdges, setShowEdges,
        showMeshStats, setShowMeshStats,
        hideTessellationEdges, setHideTessellationEdges,
        lockTranslation, setLockTranslation,
        enableNodeEditor, setEnableNodeEditor,
        enableWebsocket, setEnableWebsocket,
        autoFit, setAutoFit,
        autoConvertOnUpload, setAutoConvertOnUpload,
    } = useOptionsStore();
    const {showLegend, setShowLegend} = useColorStore();
    const {zIsUp, setZIsUp, defaultOrbitController, setDefaultOrbitController} = useModelState();
    // Section-cap colour lived in the Scene panel's Clip tab. It is a look setting you
    // pick once, not a per-cut action, so it stayed here when the rest of that tab became
    // toolbar buttons rather than following them into a strip it would have cluttered.
    const capColor = useSectionStore((st) => st.capColor);
    const setCapColor = useSectionStore((st) => st.setCapColor);

    return (
        <div className="flex flex-col gap-2">
            {/* "Show Stats" lives at the top of the Performance section — it is a
                perf-diagnosis toggle, not a display preference. */}
            <Switch
                label="Show colour legend"
                checked={showLegend}
                onChange={() => setShowLegend(!showLegend)}
            />
            <Switch
                label="Geometry edges"
                checked={showEdges}
                onChange={() => {
                    setShowEdges(!showEdges);
                    refreshEdgeOverlays();
                }}
            />
            {showEdges && (
                <div className="pl-4 border-l border-edge">
                    <Switch
                        label="Hide tessellation lines"
                        hint="Drops near-coplanar edges (the triangulation grid on curved surfaces). Keeps real feature edges and silhouettes. Smaller edge buffer ⇒ slightly faster."
                        checked={hideTessellationEdges}
                        onChange={() => {
                            setHideTessellationEdges(!hideTessellationEdges);
                            refreshEdgeOverlays();
                        }}
                    />
                </div>
            )}
            <Switch
                label="Mesh stats in Properties"
                checked={showMeshStats}
                onChange={() => setShowMeshStats(!showMeshStats)}
            />
            <Switch
                label="Auto-convert uploads to GLB"
                hint="When on, uploading a source file (STEP/IFC/FEM…) immediately queues a GLB conversion. Off (default) — upload only; convert on demand from the file row."
                checked={autoConvertOnUpload}
                onChange={() => setAutoConvertOnUpload(!autoConvertOnUpload)}
            />
            <Switch label="Auto fit to view" checked={autoFit} onChange={() => setAutoFit(!autoFit)} />
            <Switch
                label="Lock translation"
                checked={lockTranslation}
                onChange={() => setLockTranslation(!lockTranslation)}
            />
            <Switch
                label="Enable node editor"
                checked={enableNodeEditor}
                onChange={() => setEnableNodeEditor(!enableNodeEditor)}
            />
            <Switch
                label="Enable websocket"
                checked={enableWebsocket}
                onChange={() => setEnableWebsocket(!enableWebsocket)}
            />
            <Switch label="Z is up" checked={zIsUp} onChange={() => setZIsUp(!zIsUp)} />
            <Switch
                label="Use default orbit controller"
                checked={defaultOrbitController}
                onChange={() => setDefaultOrbitController(!defaultOrbitController)}
            />
            <label className="flex items-center justify-between gap-3 text-sm">
                <span>
                    Section cap colour
                    <span className="block text-xs text-content-muted">
                        Fills the cut face so a clipped solid does not read as hollow.
                    </span>
                </span>
                <input
                    type="color"
                    className="h-control-sm w-10 shrink-0 cursor-pointer rounded-sm border border-edge bg-surface-2"
                    value={capColor}
                    onChange={(e) => setCapColor(e.target.value)}
                    aria-label="Section cap colour"
                />
            </label>
        </div>
    );
};

export default DisplayOptions;
