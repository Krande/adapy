// Registration of the built-in adapy viewer UI as a UI shell.
//
// The stock UI is not privileged: it goes through `registerUiShell` exactly like
// an overlaid alternative UI does. That is what keeps the mechanism honest — if
// mounting core through the shell registry needed a special case, so would every
// other shell.
//
// Kept in its own module (not in `index.ts`) so unit tests can register the core
// shell without importing the app.

import { CORE_UI_SHELL_ID, registerUiShell } from "./uiShells";

export function registerCoreUiShell(): void {
  registerUiShell(
    {
      id: CORE_UI_SHELL_ID,
      label: "Classic",
      description: "The built-in adapy viewer UI.",
      order: 0,
      // Lazy: in the code-split hosted build this keeps the core UI out of the
      // entry chunk when another shell is active. The embed build inlines all
      // chunks, so there it is a no-op.
      load: () => import("@/app"),
    },
    CORE_UI_SHELL_ID,
  );
}
