// One shown joint's overlays (Connections > Review, "joints shown"): the switches Connections >
// Overlays has for the joint being developed, for this joint alone -- member axes, weld labels with
// their symbol and name and throat, the welds shown or not and in a colour of their own. Drawn by
// `utils/scene/jointFocusOverlay`, which importing this starts.

import React from "react";

import {DEFAULT_OVERLAYS, type ConnectionOverlays} from "@/state/connectionBuilderStore";
import {useJointFocusStore, type FocusedJoint} from "@/state/jointFocusStore";
import "@/utils/scene/jointFocusOverlay";

const Check: React.FC<{
    checked: boolean;
    onChange: (on: boolean) => void;
    label: string;
    indent?: boolean;
    disabled?: boolean;
    title?: string;
    children?: React.ReactNode;
}> = ({checked, onChange, label, indent, disabled, title, children}) => (
    <label className={"flex items-center gap-1.5 cursor-pointer " + (indent ? "pl-5 " : "") + (disabled ? "opacity-50" : "")} title={title}>
        <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        <span>{label}</span>
        {children}
    </label>
);

const JointOverlayToggles: React.FC<{joint: FocusedJoint}> = ({joint}) => {
    const setOverlay = useJointFocusStore((s) => s.setOverlay);
    const o = joint.overlays;
    const set = <K extends keyof ConnectionOverlays>(k: K, v: ConnectionOverlays[K]) => setOverlay(joint.file, joint.name, k, v);
    const toggle = (k: keyof ConnectionOverlays) => (on: boolean) => set(k, on as never);
    return (
        <div className="flex flex-col gap-0.5 pt-1 border-t border-white/10" data-joint-overlays={joint.name}>
            <span className="text-[10px] uppercase tracking-wide text-gray-400">Overlays for this joint only</span>
            <Check checked={o.axes} onChange={toggle("axes")} label="Member axes" title="Each member's axis and role, with its section or thickness" />
            <Check checked={o.welds} onChange={toggle("welds")} label="Weld labels" title="Each weld's type where it runs" />
            <Check indent disabled={!o.welds} checked={o.weldSymbols} onChange={toggle("weldSymbols")} label="Weld symbol" />
            <Check indent disabled={!o.welds} checked={o.weldDetails} onChange={toggle("weldDetails")} label="Name and throat" />
            <Check checked={o.showWelds} onChange={toggle("showWelds")} label="Show welds" title="Off, the members' end preparation shows where the welds fill it" />
            <Check disabled={!o.showWelds} checked={o.tintWelds} onChange={toggle("tintWelds")} label="Weld colour">
                <input
                    type="color"
                    value={o.weldColour}
                    disabled={!o.tintWelds || !o.showWelds}
                    onChange={(e) => set("weldColour", e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    className="w-6 h-4 p-0 border-0 bg-transparent cursor-pointer disabled:cursor-default"
                    aria-label={`Weld colour of ${joint.name}`}
                />
                {o.weldColour !== DEFAULT_OVERLAYS.weldColour && (
                    <button
                        type="button"
                        className="text-[10px] text-gray-400 hover:text-white"
                        onClick={(e) => {
                            e.preventDefault();
                            set("weldColour", DEFAULT_OVERLAYS.weldColour);
                        }}
                    >
                        reset
                    </button>
                )}
            </Check>
        </div>
    );
};

export default JointOverlayToggles;
