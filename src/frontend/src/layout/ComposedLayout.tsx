// A UI shell composed from core (`UiShellSpec` without `load`): core's layout of
// floating and dockable panels, in a document of the shell's own, starting from
// the shell's grid, under the shell's header and theme tokens. `AppRoot` (in
// `@/app`) wraps it in the routes, the sign-in gate and the viewer provider that
// every shell shares.

import React, {Suspense, useEffect, useState} from "react";

import type {UiShellSpec} from "@/plugins/uiShells";

import LayoutRoot from "./LayoutRoot";
import {setClassicContext, setClassicToolbar} from "./presets/classicPlace";

/** Set `tokens` on the document root while mounted. Portalled overlays read them too. */
function useThemeTokens(tokens: Record<string, string> | undefined): void {
  useEffect(() => {
    if (!tokens) return;
    const root = document.documentElement.style;
    const before = Object.keys(tokens).map((k) => [k, root.getPropertyValue(k)] as const);
    for (const [k, v] of Object.entries(tokens)) root.setProperty(k, v);
    return () => {
      for (const [k, v] of before) {
        if (v) root.setProperty(k, v);
        else root.removeProperty(k);
      }
    };
  }, [tokens]);
}

export default function ComposedLayout({spec}: {spec: UiShellSpec}) {
  // Before the first render of the frame, which reads them.
  useState(() => {
    setClassicContext(spec.id, spec.layout?.grid, spec.layout?.open, spec.layout?.docks);
    setClassicToolbar(spec.chrome?.toolbar !== false);
    return null;
  });
  useThemeTokens(spec.themeTokens);
  const Header = React.useMemo(() => (spec.chrome?.header ? React.lazy(spec.chrome.header) : null), [spec]);
  return (
    <div className="flex h-full w-full flex-col">
      {Header && (
        <Suspense fallback={null}>
          <Header />
        </Suspense>
      )}
      <div className="relative min-h-0 w-full flex-1">
        <LayoutRoot />
      </div>
    </div>
  );
}
