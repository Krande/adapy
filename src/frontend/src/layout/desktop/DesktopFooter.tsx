// The Desktop UI's status bar, drawn by core below the layout: the connection,
// what is loaded and selected, the active mode, and what Undo would undo. Every
// value is read from a store core already keeps.

import React from "react";

import {runtime} from "@/runtime/config";
import {useModelState} from "@/state/modelState";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {useWebsocketStatusStore} from "@/state/websocketStatusStore";

import {selectionCount} from "../coreCommands";
import {useShellMode} from "../ShellModeSwitcher";
import {DESKTOP_BAR} from "./desktopShell";

/** The connection, as the status bar names it. */
export function connectionLabel(o: {rest: boolean; wsOff: boolean; connected: boolean}): {text: string; ok: boolean | null} {
  if (o.rest) return {text: "Server", ok: true};
  if (o.wsOff) return {text: "Offline view", ok: null};
  return o.connected ? {text: "Connected", ok: true} : {text: "Not connected", ok: false};
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

const Item: React.FC<{children: React.ReactNode; title?: string}> = ({children, title}) => (
  <span className="flex items-center gap-1.5 whitespace-nowrap" title={title}>
    {children}
  </span>
);

const DesktopFooter: React.FC = () => {
  const connected = useWebsocketStatusStore((s) => s.connected);
  const models = useModelState((s) => s.loadedSourceNames.size);
  const selected = useSelectedObjectStore((s) => selectionCount(s.selectedObjects));
  const lastAction = useViewerHistoryStore((s) => s.past[s.past.length - 1]?.label ?? null);
  const {modes, active} = useShellMode();
  const mode = modes.find((m) => m.id === active);
  const conn = connectionLabel({rest: runtime.isRestMode(), wsOff: runtime.websocketDeactivated(), connected});
  const dot = conn.ok === null ? "bg-gray-500" : conn.ok ? "bg-[var(--ada-pass,#22c55e)]" : "bg-[var(--ada-fail,#ef4444)]";
  return (
    <div className={`${DESKTOP_BAR} h-6 gap-4 border-t px-2 text-[11px]`} role="status" data-desktop-footer="">
      <Item title="Connection">
        <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${dot}`} />
        {conn.text}
      </Item>
      <Item>{plural(models, "model")} loaded</Item>
      <Item>{selected ? `${plural(selected, "object")} selected` : "Nothing selected"}</Item>
      {mode && <Item title="Mode">Mode: {mode.label}</Item>}
      {lastAction && (
        <Item title="What Undo (Ctrl+Z) undoes">
          <span className="text-[var(--ada-panel-text-muted,#9ca3af)]">Undo: {lastAction}</span>
        </Item>
      )}
      <span className="ml-auto text-[var(--ada-panel-text-muted,#9ca3af)]">Desktop UI</span>
    </div>
  );
};

export default DesktopFooter;
