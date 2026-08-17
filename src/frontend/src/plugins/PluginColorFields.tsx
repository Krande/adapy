// Surfaces plugin-contributed scene color fields as an additive picker. Selecting
// one calls the provider's `resolve()` and routes the result through core's paint
// + legend path (`ctx.scene.paintField` -> colorLegendStore). Rendered alongside
// the FEA field picker in the FEM sidebar; a no-op (renders nothing) when no
// plugin advertises a color field, so existing behaviour is unchanged.
//
// A later phase folds these entries directly into the FEA field <select> as a
// named optgroup once the first real color-field plugin lands; the scaffold keeps
// them in their own row so the core FEA picker logic stays untouched.

import React from "react";

import { useViewerStores } from "@/state/AdaViewerContext";
import { makePluginContext } from "./context";
import { getSceneColorFieldProviders, disablePlugin } from "./registry";

export const PluginColorFields: React.FC = () => {
  const stores = useViewerStores();
  const baseCtx = makePluginContext("", stores);
  const providers = getSceneColorFieldProviders(baseCtx);
  const [active, setActive] = React.useState<string>("");

  if (providers.length === 0) return null;

  const onPick = async (fieldId: string) => {
    setActive(fieldId);
    if (!fieldId) return;
    const entry = providers.find((p) => p.provider.id === fieldId);
    if (!entry) return;
    const ctx = makePluginContext(entry.pluginId, stores);
    try {
      const result = await entry.provider.resolve(ctx, { field: fieldId });
      ctx.scene.paintField(fieldId, result);
    } catch (err) {
      disablePlugin(entry.pluginId, `colorField resolve threw: ${String(err)}`);
    }
  };

  return (
    <label className="flex items-center gap-1 text-xs text-white" data-testid="plugin-color-fields">
      <span className="text-gray-300 shrink-0">Plugin field</span>
      <select
        className="text-black bg-white rounded-sm px-1 py-0.5 min-w-0 flex-1 truncate"
        value={active}
        onChange={(e) => void onPick(e.target.value)}
      >
        <option value="">(none)</option>
        {providers.map(({ provider }) => (
          <option key={provider.id} value={provider.id}>
            {provider.label}
          </option>
        ))}
      </select>
    </label>
  );
};

export default PluginColorFields;
