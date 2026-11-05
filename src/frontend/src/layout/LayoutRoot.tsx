// The layout engine's root: the user's preset, the "mode" (`LayoutPrefs.activePreset`).
//
// Classic unless the user chose another. The preset is chosen once per page
// load, after the saved prefs are loaded: switching presets would remount the
// viewport, and the WebGL canvas cannot be re-parented, so a switch reloads the
// page (`./presetChoice`). Each preset loads as a chunk of its own.

import React, {Suspense, useEffect, useRef} from "react";
import {useStore} from "zustand";

import {DOCKED_PRESET_ID} from "./layout";
import {layoutStore} from "./layoutStore";
import {viewerLayoutPersistence} from "./viewerPersistence";
import type {PresetId} from "./panel";
import {resolvePreset} from "./presetChoice";

const ClassicLayout = React.lazy(() => import("./presets/classic"));
const DockedLayout = React.lazy(() => import("./presets/dock"));

export default function LayoutRoot() {
  const hydrated = useStore(layoutStore, (s) => s.hydrated);
  useEffect(() => {
    if (!layoutStore.getState().hydrated) void layoutStore.getState().hydrate(viewerLayoutPersistence());
  }, []);
  const chosen = useRef<PresetId | null>(null);
  if (!hydrated) return null;
  chosen.current ??= resolvePreset(layoutStore.getState().prefs.activePreset);
  return <Suspense fallback={null}>{chosen.current === DOCKED_PRESET_ID ? <DockedLayout /> : <ClassicLayout />}</Suspense>;
}
