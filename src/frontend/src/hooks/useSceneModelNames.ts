// What a loaded model is called anywhere outside the Scene tree -- the loaded-models list, the
// take-off -- so every place names it the way its root row in the tree does (`rootLabels`), and
// follows the Names / IDs toggle with it.

import { useCallback, useMemo } from "react";

import { useTreeViewStore } from "@/state/treeViewStore";

/** The provider a model was loaded from, when it came through the asset browser
 *  (`assets:<provider>/<collection>/...`, `assetSourceName`); null for anything else. */
export function providerOf(sourceName: string): string | null {
  const m = /^assets:([^/]+)\//.exec(sourceName);
  return m ? m[1] : null;
}

/** `name(sourceName, { withProvider })`: the model's root-row label in the Scene tree, else the
 *  last segment of its source name. With `withProvider`, a model from a provider gets it appended
 *  unless the label already says it -- for a list where models from two providers sit together. */
export function useSceneModelNames(): (sourceName: string, opts?: { withProvider?: boolean }) => string {
  const treeData = useTreeViewStore((s) => s.treeData);
  const labels = useMemo(() => {
    const out = new Map<string, string>();
    for (const root of treeData?.children ?? []) if (root.source_name) out.set(root.source_name, root.name);
    return out;
  }, [treeData]);
  return useCallback(
    (sourceName, opts) => {
      const label = labels.get(sourceName) ?? sourceName.split("/").pop() ?? sourceName;
      const provider = opts?.withProvider ? providerOf(sourceName) : null;
      return provider && !label.includes(provider) ? `${label} · ${provider}` : label;
    },
    [labels],
  );
}
