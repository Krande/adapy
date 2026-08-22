// The alternative UI itself — deliberately minimal, because its job is to prove
// the contract, not to be a second viewer:
//
//   * it is the ROOT of the React tree (core renders it in place of `App`), so
//     it owns providers, routing and layout;
//   * it reuses core's business logic verbatim — the same `AdaViewerProvider`,
//     the same `CanvasWrapper` (and therefore the same three.js scene, camera
//     controllers, selection and streaming loaders), the same URL-param loading
//     hook. Nothing about the scene is re-implemented;
//   * it renders `UiShellSwitcher`, so a user can always get back. Even without
//     it, `?ui=core` is guaranteed by the host.
//
// A real alternative UI replaces the chrome below with its own shell (docks,
// menu bar, design system) while keeping these same imports.

import React from "react";

import CanvasWrapper from "@/components/viewer/CanvasWrapper";
import ResizableTreeView from "@/components/tree_view/ResizableTreeView";
import { useUrlParamLoad } from "@/hooks/useUrlParamLoad";
import { UiShellSwitcher } from "@/plugins";
import { AdaViewerProvider } from "@/state/AdaViewerContext";

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

        {/* The only chrome this scaffold ships: a title strip and the shell
            switcher, so the swap is visible and reversible in the browser. */}
        <div className="pointer-events-none absolute left-0 top-0 z-10 flex w-full flex-row items-center gap-2 p-2">
          <div className="pointer-events-auto rounded-sm bg-emerald-800/90 px-2 py-1 text-xs font-semibold text-white">
            Alt UI (plugin shell)
          </div>
          <div className="pointer-events-auto">
            <UiShellSwitcher className="rounded-sm bg-gray-800/90 px-2 py-1" />
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
