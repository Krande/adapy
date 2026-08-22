// Reference UI-SHELL plugin — the template an out-of-tree alternative viewer UI
// copies. Where the `demo` plugin proves the in-UI slots (panels, buttons,
// colour fields), this one proves the other axis: contributing an ENTIRE root
// UI that core mounts instead of its own.
//
// The shape a real alternative-UI repo takes:
//
//   my-adapy-ui/                     <- its own git repo, its own release cycle
//     package.json                   <- name: "@adapy-plugins/my-ui"
//     plugin.manifest.json
//     src/register.tsx               <- exactly this file's shape
//     src/**                         <- the UI itself
//
// At image-build time CI clones it to `src/frontend/packages/plugins/my-ui/`,
// sets `EXTRA_PLUGINS_ENABLE=my-ui` (so `gen:plugins` registers it) and
// `UI_DEFAULT=my-ui` (so the image boots into it). adapy itself never names the
// repo — see deploy/Dockerfile.viewer.
//
// The UI imports core through ONE named surface — `@/viewer-core` (+ its
// `/scene` and `/plugins` entry points). That facade is the contract: stores,
// services, the REST client and the canvas are the shared substrate, only the
// chrome is replaced, and a shell never reaches into core internals (the fence
// is enforced by src/__tests__/plugins/viewerCoreFacade.test.ts).

import { registerPlugin } from "@/viewer-core";

export function register(): void {
  registerPlugin({
    id: "ui-alt",
    version: "0.1.0",
    coreApiRange: ">=1.0 <2.0",
    schemaVersion: 1,
    uiShells: [
      {
        id: "alt",
        label: "Alt UI",
        description: "Reference alternative viewer UI (plugin-contributed shell).",
        order: 10,
        // Lazily loaded: the shell's code only downloads when it is the active
        // one, so a build carrying both UIs does not pay for both at startup.
        load: () => import("./AltShell"),
      },
    ],
  });
}

export default register;
