// The shell's status bar, drawn by core below the layout: the active mode, and
// the shell's version. A real shell shows its own state here (connection,
// selection, a compile gate); core only gives it the slot.

import React from "react";

import { useShellMode } from "@/viewer-core/layout";

const AltFooter: React.FC = () => {
  const { modes, active } = useShellMode();
  const mode = modes.find((m) => m.id === active);
  return (
    <div className="flex h-6 shrink-0 items-center gap-3 border-t border-gray-700 bg-gray-950 px-2 text-[11px] text-gray-400" role="status">
      <span>Mode: {mode?.label ?? "none"}</span>
      <span className="ml-auto">Alt UI 0.3.0</span>
    </div>
  );
};

export default AltFooter;
