// Applies the active mode's scene scope (`ModeBehaviour.scene`, see ./sceneScopeRules) to the
// loaded models. Headless; mounted once by the layout. It does nothing until a UI's mode declares a
// scope: core's own UI (no modes), and a shell that scopes its scene itself, are never touched.

import { useEffect, useRef, useSyncExternalStore } from "react";

import { loadedSourceGroups, useModelState } from "@/state/modelState";
import { requestRender } from "@/state/perfStore";

import { applyModeSceneScope, newSceneScopeMemory, type ScopedGroup } from "./sceneScopeRules";
import { activeModeDef, shellModesVersion, subscribeShellModes } from "./shellModes";

export function ModeSceneScope(): null {
  const version = useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  const loaded = useModelState((s) => s.loadedSourceNames);
  const memory = useRef(newSceneScopeMemory());
  // Whether a pass has scoped anything yet: until then there is nothing to restore either.
  const engaged = useRef(false);

  useEffect(() => {
    const mode = activeModeDef();
    const scope = mode?.behaviour?.scene ?? null;
    if (!scope && !engaged.current) return;
    engaged.current = !!scope || memory.current.before.size > 0;
    const groups = new Map<string, ScopedGroup>();
    for (const name of loaded) {
      const group = loadedSourceGroups.get(name);
      if (group) groups.set(name, group);
    }
    if (applyModeSceneScope(mode?.id ?? null, scope, groups, memory.current)) requestRender();
    if (!scope) engaged.current = false;
  }, [version, loaded]);

  return null;
}
