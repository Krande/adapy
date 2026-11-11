import React from "react";

import {runtime} from "@/runtime/config";
import {useClashCheckStore} from "@/state/clashCheckStore";
import {DEFAULT_OVERLAYS, useConnectionBuilderStore, type ConnectionOverlays} from "@/state/connectionBuilderStore";

/** Scene > Joints > Overlays: everything the joint tabs draw in 3D, each a toggle. The joint being
 *  developed (`ada dev connections`) where one is, then the clash run's markers and contacts -- the
 *  same switches the Clashes tab has beside its list, so either place turns them off. */
const JointOverlaysPanel: React.FC = () => (
    <div className="flex flex-col gap-3 text-sm">
        {runtime.devConnections() && <ConnectionSection />}
        <ClashSection />
    </div>
);

const Toggle: React.FC<{
    checked: boolean;
    onChange: (on: boolean) => void;
    label: string;
    description?: string;
    indent?: boolean;
    disabled?: boolean;
    children?: React.ReactNode;
}> = ({checked, onChange, label, description, indent, disabled, children}) => (
    <div className={"flex flex-col gap-0.5 " + (indent ? "pl-6 " : "") + (disabled ? "opacity-50" : "")}>
        <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
            <span className="font-semibold">{label}</span>
            {children}
        </label>
        {description && <p className="text-xs text-gray-400 pl-6">{description}</p>}
    </div>
);

const Heading: React.FC<{children: React.ReactNode}> = ({children}) => (
    <span className="text-xs uppercase tracking-wide text-gray-400">{children}</span>
);

const ConnectionSection = () => {
    const o = useConnectionBuilderStore((s) => s.overlays);
    const set = useConnectionBuilderStore((s) => s.setOverlay);
    const toggle = (k: keyof ConnectionOverlays) => (on: boolean) => set(k, on as never);
    return (
        <div className="flex flex-col gap-1.5 pb-2 border-b border-white/10">
            <Heading>The joint being developed</Heading>
            <Toggle
                checked={o.axes}
                onChange={toggle("axes")}
                label="Member axes"
                description="Each member's axis and role (incoming amber, landing teal), with its section or thickness."
            />
            <Toggle
                checked={o.welds}
                onChange={toggle("welds")}
                label="Weld labels"
                description="Each weld's type where it meets its members; red where it does not touch one it joins."
            />
            <Toggle indent disabled={!o.welds} checked={o.weldSymbols} onChange={toggle("weldSymbols")} label="Weld symbol" />
            <Toggle
                indent
                disabled={!o.welds}
                checked={o.weldDetails}
                onChange={toggle("weldDetails")}
                label="Name and throat"
            />
            <Toggle
                checked={o.showWelds}
                onChange={toggle("showWelds")}
                label="Show welds"
                description="Off, the members' end preparation (a bevelled groove) shows where the welds fill it."
            />
            <Toggle
                disabled={!o.showWelds}
                checked={o.tintWelds}
                onChange={toggle("tintWelds")}
                label="Weld colour"
                description="Draw the welds in a colour of their own, apart from the members' steel."
            >
                <input
                    type="color"
                    value={o.weldColour}
                    disabled={!o.tintWelds}
                    onChange={(e) => set("weldColour", e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    className="w-7 h-5 p-0 border-0 bg-transparent cursor-pointer disabled:cursor-default"
                    aria-label="Weld colour"
                />
                {o.weldColour !== DEFAULT_OVERLAYS.weldColour && (
                    <button
                        type="button"
                        className="text-xs text-gray-400 hover:text-[var(--ada-panel-text)]"
                        onClick={(e) => {
                            e.preventDefault();
                            set("weldColour", DEFAULT_OVERLAYS.weldColour);
                        }}
                    >
                        reset
                    </button>
                )}
            </Toggle>
        </div>
    );
};

const ClashSection = () => {
    const showMarkers = useClashCheckStore((s) => s.showMarkers);
    const setShowMarkers = useClashCheckStore((s) => s.setShowMarkers);
    const markerScale = useClashCheckStore((s) => s.markerScale);
    const setMarkerScale = useClashCheckStore((s) => s.setMarkerScale);
    const showContacts = useClashCheckStore((s) => s.showContacts);
    const setShowContacts = useClashCheckStore((s) => s.setShowContacts);
    return (
        <div className="flex flex-col gap-1.5">
            <Heading>Clashes</Heading>
            <Toggle
                checked={showMarkers}
                onChange={setShowMarkers}
                label="Joint markers"
                description="A coloured sphere at every joint centre the clash run found."
            />
            <label className={"flex items-center gap-2 pl-6 text-xs text-gray-400 " + (showMarkers ? "" : "opacity-50")}>
                size
                <input
                    type="range"
                    // Logarithmic, the default in the middle: 0.1x at the left, 10x at the right.
                    min={-100}
                    max={100}
                    step={5}
                    disabled={!showMarkers}
                    value={Math.round(Math.log10(markerScale) * 100)}
                    onChange={(e) => setMarkerScale(10 ** (Number(e.target.value) / 100))}
                    onDoubleClick={() => setMarkerScale(1)}
                    className="w-28 accent-blue-500"
                    aria-label="Joint marker size"
                    title="Double-click to reset"
                />
                {markerScale.toFixed(markerScale < 1 ? 2 : 1)}×
            </label>
            <Toggle
                checked={showContacts}
                onChange={setShowContacts}
                label="Contacts"
                description="Every contact of a convex-contact run: each patch on both members in their roles' colours, and its normal."
            />
        </div>
    );
};

export default JointOverlaysPanel;
