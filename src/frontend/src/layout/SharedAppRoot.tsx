// `AppRoot` (in `@/app`) for a shell that mounts a root of its own (`load`): the
// canvas-less routes (`/auth/callback`, `/convert`, `/admin`, a Simulation
// follower, a popped-out panel), the viewer provider, the sign-in gate and deep
// links, around the shell's UI. Loaded lazily through `@/app`, the module the
// panel bodies load through, so a shell's chunk does not carry it.

import React, {Suspense} from "react";

const Lazy = React.lazy(() => import("@/app").then((m) => ({default: m.AppRoot})));

export function AppRoot({children}: {children: React.ReactNode}) {
  return (
    <Suspense fallback={null}>
      <Lazy>{children}</Lazy>
    </Suspense>
  );
}
