// The Layers panel (`core:layers`): one row per layer drawn over the model, with a switch and an
// opacity slider. The layers are whatever registered itself (state/layerRegistry.ts): core knows
// none of them by name, and the panel is not offered while there is none.

import React, { useSyncExternalStore } from "react";

import { PanelHeaderActions, usePanelHeaderActions } from "@/components/ui/PanelFrame";
import { listLayers, setLayerOpacity, setLayerVisible, subscribeLayers, type LayerEntry } from "@/state/layerRegistry";
import { useLayersPanelStore } from "@/state/layersPanelStore";

const LayerRow: React.FC<{ layer: LayerEntry }> = ({ layer }) => (
  <li className="flex items-center gap-2" data-layer-id={layer.id}>
    <input
      type="checkbox"
      role="switch"
      aria-label={`Show ${layer.label}`}
      checked={layer.visible}
      onChange={(e) => setLayerVisible(layer.id, e.target.checked)}
    />
    <span className={`flex-1 truncate ${layer.visible ? "" : "text-gray-400"}`} title={layer.label}>
      {layer.label}
    </span>
    <input
      type="range"
      aria-label={`Opacity of ${layer.label}`}
      className="w-24"
      min={0}
      max={1}
      step={0.05}
      value={layer.opacity}
      disabled={!layer.visible}
      onChange={(e) => setLayerOpacity(layer.id, Number(e.target.value))}
    />
    <span className="w-8 text-right tabular-nums text-gray-300">{Math.round(layer.opacity * 100)}%</span>
  </li>
);

/** The rows, grouped: layers without a group first, then each group under its name. */
export const LayersBody: React.FC = () => {
  const layers = useSyncExternalStore(subscribeLayers, listLayers, listLayers);
  if (layers.length === 0) return <div className="text-xs text-gray-400">No layers.</div>;
  const groups = new Map<string, LayerEntry[]>();
  for (const layer of layers) {
    const key = layer.group ?? "";
    groups.set(key, [...(groups.get(key) ?? []), layer]);
  }
  return (
    <div className="flex flex-col gap-2 text-xs text-white" data-layers-panel="true">
      {[...groups].map(([group, rows]) => (
        <div key={group} className="flex flex-col gap-1">
          {group && <div className="text-gray-400 uppercase tracking-wide">{group}</div>}
          <ul className="flex flex-col gap-1">
            {rows.map((layer) => (
              <LayerRow key={layer.id} layer={layer} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
};

/** The panel in the classic UI's card: shown while its flag is set. */
const LayersPanel: React.FC = () => {
  const open = useLayersPanelStore((s) => s.panelOpen);
  const hosted = usePanelHeaderActions().length > 0;
  if (!open) return null;
  return (
    <div className="flex flex-col gap-2 text-xs text-white p-2 bg-gray-900/70 rounded-md min-w-[300px] max-w-[420px]">
      <div className="flex items-center gap-2">
        <span className="font-bold">Layers</span>
        {hosted && <PanelHeaderActions />}
      </div>
      <LayersBody />
    </div>
  );
};

export default LayersPanel;
