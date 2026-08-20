import React from "react";
import {useTypeIconsStore} from "@/state/typeIconsStore";
import {Section} from "./boxedSection";

// Type-icon overlay toggles: a Factorio-style layer of icons over the model —
// archetype icons on equipment (⚡ electrical, P pump, T tank), fluid/service
// markers along runs (💧 water, black oil drop, ⚡ electrical), and a red "!"
// over equipment with unconnected inputs.
export const IconOverlaySection: React.FC = () => {
  const icons = useTypeIconsStore();
  return (
    <div className="flex flex-col gap-1">
      <label className="flex items-center gap-1 font-semibold">
        <input
          type="checkbox"
          checked={icons.enabled}
          onChange={(e) => icons.setEnabled(e.target.checked)}
        />
        Type icons
      </label>
      {icons.enabled && (
        <div className="flex items-center gap-3 flex-wrap pl-5 text-content">
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={icons.showEquipment}
              onChange={(e) => icons.setShowEquipment(e.target.checked)}
            />
            equipment
          </label>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={icons.showMedia}
              onChange={(e) => icons.setShowMedia(e.target.checked)}
            />
            media
          </label>
          <label
            className="flex items-center gap-1"
            title="Red ! over equipment with unconnected inputs"
          >
            <input
              type="checkbox"
              checked={icons.showMissing}
              onChange={(e) => icons.setShowMissing(e.target.checked)}
            />
            missing inputs
          </label>
        </div>
      )}
    </div>
  );
};
