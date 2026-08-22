// Demo plugin — the reference implementation that exercises EVERY plugin slot,
// proving the framework end-to-end. It is dormant by default (see `isDemoEnabled`)
// so shipping it changes nothing for existing users; enable with `?plugins=demo`.
//
// This is the shape every real plugin follows: a package exporting `register()`
// that makes a single `registerPlugin({...})` call at import into the core
// registry. Core never imports this file by name — the generated build-time
// registry (`@/plugins/registry.generated`) does.

import React from "react";

import {
  registerPlugin,
  type AdaPluginContext,
  type SceneColorFieldResult,
} from "@/viewer-core";
import { isDemoEnabled } from "./enabled";

// A tiny inline icon so the top-bar button has a glyph without pulling a shared
// core icon (a plugin ships its own assets).
const DemoIcon: React.FC = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="w-6 h-6"
    aria-hidden="true"
  >
    <path d="M12 2 3 7v10l9 5 9-5V7z" />
    <path d="M12 22V12" />
    <path d="m3 7 9 5 9-5" />
  </svg>
);

const DemoTopPanel: React.FC<{ ctx: AdaPluginContext }> = ({ ctx }) => (
  <div
    data-testid="demo-top-panel"
    className="rounded-md border border-blue-700/60 bg-gray-800/95 p-2 text-xs text-gray-100"
  >
    <div className="font-semibold text-blue-300">Demo plugin</div>
    <div className="mt-0.5 text-gray-300">
      Hello from a plugin — scope <code>{ctx.scope() || "(none)"}</code>. This panel,
      its top-bar button, a sidecar loader, a colour field and a URL param are all
      mounted through the plugin slots.
    </div>
  </div>
);

const DemoFemSidebarPanel: React.FC<{ ctx: AdaPluginContext }> = () => (
  <div
    data-testid="demo-fem-sidebar-panel"
    className="rounded-sm border border-violet-700/50 bg-gray-900/40 p-2 text-xs text-violet-200"
  >
    Demo FEM-sidebar panel (region <code>fem-sidebar</code>).
  </div>
);

/** Register the demo plugin into the core registry. Called by the generated
 * build-time registry; idempotent-by-id at the registry level. */
export function register(): void {
  registerPlugin({
    id: "demo",
    version: "0.1.0",
    coreApiRange: ">=1.0 <2.0",
    schemaVersion: 1,
    // Whole-plugin gate: dormant unless explicitly enabled.
    activationPredicate: () => isDemoEnabled(),
    panels: [
      {
        id: "hello",
        region: "top-panel",
        order: 10,
        render: (ctx) => <DemoTopPanel ctx={ctx} />,
        topBarButton: { icon: DemoIcon, label: "Demo plugin" },
      },
      {
        id: "fem",
        region: "fem-sidebar",
        order: 10,
        activationPredicate: () => true,
        render: (ctx) => <DemoFemSidebarPanel ctx={ctx} />,
      },
    ],
    sceneColorFields: [
      {
        id: "uf",
        label: "Demo utilisation",
        supports: "element",
        available: () => true,
        resolve: async (): Promise<SceneColorFieldResult> => ({
          // Trivial deterministic field: three elements ramping 0 -> 1.
          values: new Float32Array([0.0, 0.5, 1.0]),
          range: [0, 1],
          colorFor: (v) => [v, 0, 1 - v],
        }),
      },
    ],
    resultSidecarLoaders: [
      {
        id: "hello-loader",
        // Detect on either the reserved manifest map or the enabled flag (so the
        // scaffold's loader runs even for a plain geometry load with no manifest).
        detect: (manifest) =>
          isDemoEnabled() ||
          !!(manifest as { plugins?: Record<string, unknown> } | null)?.plugins?.["demo"],
        load: async (ctx) => {
          ctx.log("info", "demo sidecar loader ran");
          // A real loader would fetch its `{sidecarPrefix}.*` blobs + add scene
          // objects here. The demo is a no-op that returns a disposer.
          return () => ctx.log("info", "demo sidecar loader disposed");
        },
      },
    ],
    urlParamHandlers: [
      {
        params: ["demoParam"],
        handle: async (ctx, values) => {
          ctx.log("info", `demo url param consumed: ${JSON.stringify(values)}`);
          return true;
        },
      },
    ],
  });
}

export default register;
