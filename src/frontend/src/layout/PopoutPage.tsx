// A panel alone in a browser window of its own (`?popout=<panelId>`, see
// `./popout`): its title bar and its content, nothing else. `AppRoot` mounts it
// under the viewer provider and, in hosted mode, the sign-in gate, without the
// canvas or the deep-link loading.

import React, {useEffect} from "react";

import {WINDOW_HEADER} from "@/components/ui/panelChrome";
import {activeUiShellId, getUiShell} from "@/plugins/uiShells";

import {registerCorePanels} from "./corePanels";
import {PanelMount} from "./LayoutHost";
import type {PanelId, PanelProps} from "./panel";
import {canPopOut} from "./popout";
import {getPanel} from "./registry";

/** Where a pop-out window's panel says it is hosted. */
export const POPOUT_ZONE = "popout";

export default function PopoutPage({panelId}: {panelId: PanelId}) {
  registerCorePanels();
  const panel = getPanel(panelId);
  const ok = !!panel && canPopOut(panel);
  useEffect(() => {
    if (ok) document.title = panel!.title;
  }, [ok, panel]);
  // The active shell's look, as in the window it came from.
  useEffect(() => {
    const tokens = getUiShell(activeUiShellId())?.themeTokens;
    for (const [k, v] of Object.entries(tokens ?? {})) document.documentElement.style.setProperty(k, v);
  }, []);
  if (!ok) {
    return (
      <div className="flex h-[100dvh] w-full items-center justify-center bg-gray-900 p-4 text-sm text-gray-300">
        This panel cannot open in a window of its own.
      </div>
    );
  }
  const props: PanelProps = {panelId, host: {kind: "window", zone: POPOUT_ZONE}, close: () => window.close(), isActive: true};
  return (
    <div className="flex h-[100dvh] w-full flex-col bg-[var(--ada-window-bg,#111827)] text-[var(--ada-panel-text)]">
      <div className={WINDOW_HEADER}>
        <span className="truncate text-sm font-medium">{panel!.title}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2 text-sm">
        <PanelMount panel={panel!} props={props} which={panel!.content ? "content" : "body"} />
      </div>
    </div>
  );
}
