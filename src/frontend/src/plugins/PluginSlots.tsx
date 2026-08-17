// Core "slot host" components. These iterate the plugin registry and mount
// whatever plugins contributed — core never names a plugin. Every plugin panel
// is wrapped in the shared ErrorBoundary so a panel crash is contained (and the
// plugin is disabled for the session), never white-screening the viewer.

import React from "react";

import ErrorBoundary from "@/components/common/ErrorBoundary";
import { useViewerStores } from "@/state/AdaViewerContext";
import { makePluginContext } from "./context";
import { usePluginUiStore } from "./pluginUiStore";
import {
  disablePlugin,
  getPanelsForRegion,
  type AdaPluginContext,
  type PanelSlot,
  type PluginRegion,
} from "./registry";

/** Render a single plugin panel body inside a per-plugin context. Throwing here
 * is caught by the surrounding ErrorBoundary. */
const PluginPanelBody: React.FC<{ ctx: AdaPluginContext; panel: PanelSlot }> = ({
  ctx,
  panel,
}) => {
  return <>{panel.render(ctx)}</>;
};

/** Mount every registered panel for a region. Panels with a top-bar button only
 * render while that button toggled them visible; buttonless panels render
 * whenever their activation predicate holds (data-present case).
 *
 * `excludeTabs` drops panels that opted into a Simulation tab (carry `asTab`) so
 * the region can coexist with the tabbed Simulation host without double-rendering
 * them — the tab host mounts those, this mounts the rest. */
export const PluginPanelRegion: React.FC<{ region: PluginRegion; excludeTabs?: boolean }> = ({
  region,
  excludeTabs,
}) => {
  const stores = useViewerStores();
  // Subscribe to visibility so a top-bar toggle re-renders this region.
  const visible = usePluginUiStore((s) => s.visible);

  const baseCtx = makePluginContext("", stores);
  const panels = getPanelsForRegion(region, baseCtx).filter(
    ({ panel }) => !excludeTabs || !panel.asTab,
  );
  if (panels.length === 0) return null;

  return (
    <>
      {panels
        .filter(({ panel }) => !panel.topBarButton || visible[panel.id])
        .map(({ pluginId, panel }) => {
          const ctx = makePluginContext(pluginId, stores);
          return (
            <ErrorBoundary
              key={panel.id}
              label={`Plugin ${pluginId}`}
              fallback={(error, reset) => {
                disablePlugin(pluginId, `panel render threw: ${error.message}`);
                return (
                  <div className="rounded-md border border-red-700/60 bg-gray-800/95 p-2 text-xs text-gray-100">
                    <div className="font-semibold text-red-300">
                      Plugin “{pluginId}” hit an error
                    </div>
                    <div className="mt-0.5 mb-1.5 break-words text-gray-400">
                      {error.message}
                    </div>
                    <button
                      type="button"
                      onClick={reset}
                      className="rounded-sm bg-gray-700 px-2 py-1 text-white hover:bg-gray-600"
                    >
                      Retry
                    </button>
                  </div>
                );
              }}
            >
              <PluginPanelBody ctx={ctx} panel={panel} />
            </ErrorBoundary>
          );
        })}
    </>
  );
};

/** Top-bar buttons contributed by plugins (region "top-panel", panels carrying a
 * `topBarButton`). Each toggles its panel's visibility in the plugin UI store. */
export const PluginTopBarButtons: React.FC<{ navBtnClass: (active: boolean) => string }> = ({
  navBtnClass,
}) => {
  const stores = useViewerStores();
  const visible = usePluginUiStore((s) => s.visible);
  const toggle = usePluginUiStore((s) => s.toggle);

  const baseCtx = makePluginContext("", stores);
  const panels = getPanelsForRegion("top-panel", baseCtx).filter(
    ({ panel }) => panel.topBarButton,
  );
  if (panels.length === 0) return null;

  return (
    <>
      {panels.map(({ pluginId, panel }) => {
        const btn = panel.topBarButton!;
        const Icon = btn.icon;
        const active = !!visible[panel.id];
        return (
          <button
            key={panel.id}
            className={navBtnClass(active)}
            onClick={() => {
              toggle(panel.id);
              if (btn.onClick) {
                try {
                  btn.onClick(makePluginContext(pluginId, stores));
                } catch (err) {
                  disablePlugin(pluginId, `top-bar onClick threw: ${String(err)}`);
                }
              }
            }}
            title={btn.label}
            aria-label={btn.label}
            aria-pressed={active}
          >
            <Icon />
          </button>
        );
      })}
    </>
  );
};
