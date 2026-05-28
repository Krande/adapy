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
        lockTranslation, setLockTranslation,
        enableNodeEditor, setEnableNodeEditor,
        enableWebsocket, setEnableWebsocket,
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
