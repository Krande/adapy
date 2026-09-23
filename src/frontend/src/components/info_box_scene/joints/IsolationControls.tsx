// "Show me this joint, not the frame in front of it."
//
// One control, rendered by both joint surfaces (the `Clashes` rows and the `Joints` tree), because
// it acts on the SAME cursor: whichever joint or group is current is what stays visible. Two
// copies of the state would let the two tabs disagree about what the model is currently showing.
//
// The opacity slider appears only for `ghost`, where it means something -- a hidden member has no
// opacity to set, and a disabled slider beside it would be a control that is never the answer.

import React from "react";

import { useClashCheckStore } from "@/state/clashCheckStore";

const MODES: { value: "off" | "ghost" | "hidden"; label: string; title: string }[] = [
  { value: "off", label: "show all", title: "Every member stays as it is" },
  { value: "ghost", label: "fade rest", title: "Members outside the selected joint turn translucent" },
  { value: "hidden", label: "hide rest", title: "Members outside the selected joint are hidden" },
];

const IsolationControls: React.FC = () => {
  const isolate = useClashCheckStore((s) => s.isolate);
  const opacity = useClashCheckStore((s) => s.isolateOpacity);
  const setIsolate = useClashCheckStore((s) => s.setIsolate);
  const setIsolateOpacity = useClashCheckStore((s) => s.setIsolateOpacity);

  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] text-gray-400">
      <select
        aria-label="Isolate"
        className="bg-gray-600 text-white rounded-sm text-[10px] px-1 py-0.5"
        value={isolate}
        onChange={(e) => setIsolate(e.target.value as "off" | "ghost" | "hidden")}
        title={MODES.find((m) => m.value === isolate)?.title}
      >
        {MODES.map((m) => (
          <option key={m.value} value={m.value} title={m.title}>
            {m.label}
          </option>
        ))}
      </select>
      {isolate === "ghost" && (
        <label className="flex items-center gap-1" title="How faint the faded members are">
          <input
            type="range"
            min={2}
            max={60}
            step={1}
            value={Math.round(opacity * 100)}
            onChange={(e) => setIsolateOpacity(Number(e.target.value) / 100)}
            className="w-20 accent-blue-500"
            aria-label="Fade opacity"
          />
          <span className="tabular-nums w-7">{Math.round(opacity * 100)}%</span>
        </label>
      )}
    </div>
  );
};

export default IsolationControls;
