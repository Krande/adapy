// The alternative UI itself — deliberately minimal, because its job is to prove
// the contract, not to be a second viewer:
//
//   * it is the ROOT of the React tree (core renders it in place of `App`), so
//     it owns providers, routing and layout;
//   * it reuses core's business logic verbatim, through the `@/viewer-core`
//     facade and nothing else — the same `AdaViewerProvider`, the same
//     `CanvasWrapper` (and therefore the same three.js scene, camera
//     controllers, selection and streaming loaders), the same URL-param loading
//     hook. Nothing about the scene is re-implemented;
//   * it mounts the plugin slot hosts, so feature plugins' buttons and panels
//     still appear in a UI that core knows nothing about;
//   * it renders `UiShellSwitcher`, so a user can always get back. Even without
//     it, `?ui=core` is guaranteed by the host.
//
// A real alternative UI replaces the chrome below with its own shell (docks,
// menu bar, design system) while keeping these same imports.

import React from "react";

import { AdaViewerProvider, UiShellSwitcher } from "@/viewer-core/app";
import { PluginPanelRegion, PluginTopBarButtons } from "@/viewer-core/plugins";
import { CanvasWrapper, ResizableTreeView, useUrlParamLoad } from "@/viewer-core/scene";

// Plugin buttons are styled by the HOST shell, not by the plugin — that is what
// keeps a feature plugin looking native in whichever UI mounts it.
const TOP_BAR_BTN =
  "inline-flex h-8 w-8 items-center justify-center rounded-sm bg-emerald-800/90 " +
  "text-white transition-colors pointer-fine:hover:bg-emerald-700/90";

const AltBody: React.FC = () => {
  // Same deep-link handling core does (`?file=…` etc.) — a shell that skipped
  // this would silently break every shared viewer link.
  useUrlParamLoad();

  return (
    <div className="relative flex h-full w-full flex-row bg-gray-900">
      <div className="relative h-full">
        <ResizableTreeView />
      </div>

      <div className="relative h-full w-full">
        <CanvasWrapper />

        {/* The chrome this scaffold ships: a title strip, the shell switcher,
            and the plugin slot hosts — so feature plugins keep working in a UI
            that core knows nothing about. */}
        <div className="pointer-events-none absolute left-0 top-0 z-10 flex w-full flex-col gap-2 p-2">
          <div className="pointer-events-auto flex flex-row items-center gap-2">
            <div className="rounded-sm bg-emerald-800/90 px-2 py-1 text-xs font-semibold text-white">
              Alt UI (plugin shell)
            </div>
            <UiShellSwitcher className="rounded-sm bg-gray-800/90 px-2 py-1" />
            <PluginTopBarButtons navBtnClass={() => TOP_BAR_BTN} />
          </div>
          <div className="pointer-events-auto flex flex-col gap-2">
            <PluginPanelRegion region="top-panel" />
          </div>
        </div>
      </div>
    </div>
  );
};

const AltShell: React.FC = () => (
  <AdaViewerProvider>
    <AltBody />
  </AdaViewerProvider>
);

export default AltShell;
