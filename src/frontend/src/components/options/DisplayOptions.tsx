import React from "react";
import {useOptionsStore} from "@/state/optionsStore";
import {useColorStore} from "@/state/colorLegendStore";
import {useModelState} from "@/state/modelState";

const Toggle: React.FC<{
    checked: boolean;
    onChange: () => void;
    children: React.ReactNode;
}> = ({checked, onChange, children}) => (
    <label className="flex items-center space-x-2">
        <input type="checkbox" checked={checked} onChange={onChange}/>
        <span>{children}</span>
    </label>
);

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

    return (
        <div className="space-y-2">
            {/* "Show Stats" moved to the top of the Performance
                section — it's a perf-diagnosis toggle, not a display
                preference. */}
            <Toggle checked={showLegend} onChange={() => setShowLegend(!showLegend)}>Show Color Legend</Toggle>
            <Toggle checked={showEdges} onChange={() => setShowEdges(!showEdges)}>Geometry Edges</Toggle>
            {showEdges && (
                <label className="flex items-start space-x-2 pl-6">
                    <input
                        type="checkbox"
                        className="mt-1"
                        checked={hideTessellationEdges}
                        onChange={() => setHideTessellationEdges(!hideTessellationEdges)}
                    />
                    <span className="leading-tight">
                        Hide tessellation lines
                        <span className="ml-1 text-[10px] uppercase tracking-wide text-amber-300">
                            (reload required)
                        </span>
                        <span className="block text-xs text-gray-400">
                            Drops near-coplanar edges (the triangulation grid
                            on curved surfaces). Keeps real feature edges and
                            silhouettes. Smaller edge buffer ⇒ slightly faster.
                        </span>
                    </span>
                </label>
            )}
            <Toggle checked={showMeshStats} onChange={() => setShowMeshStats(!showMeshStats)}>
                Mesh stats in Properties
            </Toggle>
            <label className="flex items-start space-x-2">
                <input
                    type="checkbox"
                    className="mt-1"
                    checked={autoConvertOnUpload}
                    onChange={() => setAutoConvertOnUpload(!autoConvertOnUpload)}
                />
                <span className="leading-tight">
                    Auto-convert uploads to GLB
                    <span className="block text-xs text-gray-400">
                        When on, uploading a source file (STEP/IFC/FEM…) immediately
                        queues a GLB conversion. Off (default) — upload only; convert
                        on demand from the file row.
                    </span>
                </span>
            </label>
            <Toggle checked={autoFit} onChange={() => setAutoFit(!autoFit)}>Auto Fit to View</Toggle>
            <Toggle checked={lockTranslation} onChange={() => setLockTranslation(!lockTranslation)}>Lock Translation</Toggle>
            <Toggle checked={enableNodeEditor} onChange={() => setEnableNodeEditor(!enableNodeEditor)}>Enable Node Editor</Toggle>
            <Toggle checked={enableWebsocket} onChange={() => setEnableWebsocket(!enableWebsocket)}>Enable Websocket</Toggle>
            <Toggle checked={zIsUp} onChange={() => setZIsUp(!zIsUp)}>Z is UP</Toggle>
            <Toggle checked={defaultOrbitController} onChange={() => setDefaultOrbitController(!defaultOrbitController)}>
                Use Default Orbitcontroller
            </Toggle>
        </div>
    );
};

export default DisplayOptions;
