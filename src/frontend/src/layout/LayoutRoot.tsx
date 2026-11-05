// The layout engine's root: the classic preset, once the saved prefs are loaded.
// The preset loads as a chunk of its own.

import React, {Suspense, useEffect} from "react";
import {useStore} from "zustand";

import {layoutStore} from "./layoutStore";
import {viewerLayoutPersistence} from "./viewerPersistence";

const ClassicLayout = React.lazy(() => import("./presets/classic"));

export default function LayoutRoot() {
  const hydrated = useStore(layoutStore, (s) => s.hydrated);
  useEffect(() => {
    if (!layoutStore.getState().hydrated) void layoutStore.getState().hydrate(viewerLayoutPersistence());
  }, []);
  if (!hydrated) return null;
  return (
    <Suspense fallback={null}>
      <ClassicLayout />
    </Suspense>
  );
}
