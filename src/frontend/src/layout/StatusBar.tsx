// The status bar a composed shell draws below its layout, and core's items for it.
//
// A shell lists what it wants in `UiShellChrome.statusItems`: core's items by id (the connection,
// what is loaded and selected, the mode, what Undo would undo, the procedural compile, the viewer's
// version) and items of its own (`StatusBarItem`), in the order given; core then draws the bar
// (`ComposedLayout`). A shell that draws its own footer mounts `StatusBar` there with the same list.
// Every core item reads a store core already keeps.

import React from "react";

import {runtime} from "@/runtime/config";
import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";
import {useModelState} from "@/state/modelState";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {useWebsocketStatusStore} from "@/state/websocketStatusStore";

import {selectionCount} from "./coreCommands";
import {useShellMode} from "./ShellModeSwitcher";
import {SHELL_BAR, isCoreStatusItem, type CoreStatusItemId, type StatusBarItem, type StatusItemSpec} from "./statusItems";

/** The connection, as the status bar names it. */
export function connectionLabel(o: {rest: boolean; wsOff: boolean; connected: boolean}): {text: string; ok: boolean | null} {
  if (o.rest) return {text: "Server", ok: true};
  if (o.wsOff) return {text: "Offline view", ok: null};
  return o.connected ? {text: "Connected", ok: true} : {text: "Not connected", ok: false};
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** One item's frame: a nowrap run of content with an optional tooltip. Draw a shell's own content in it to match core's items. */
export const StatusItem: React.FC<{children: React.ReactNode; title?: string; muted?: boolean}> = ({children, title, muted}) => (
  <span className={`flex items-center gap-1.5 whitespace-nowrap${muted ? " text-[var(--ada-panel-text-muted,#9ca3af)]" : ""}`} title={title} data-status-item="">
    {children}
  </span>
);

const ConnectionItem: React.FC = () => {
  const connected = useWebsocketStatusStore((s) => s.connected);
  const conn = connectionLabel({rest: runtime.isRestMode(), wsOff: runtime.websocketDeactivated(), connected});
  const dot = conn.ok === null ? "bg-gray-500" : conn.ok ? "bg-[var(--ada-pass,#22c55e)]" : "bg-[var(--ada-fail,#ef4444)]";
  return (
    <StatusItem title="Connection">
      <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${dot}`} />
      {conn.text}
    </StatusItem>
  );
};

const ModelsItem: React.FC = () => {
  const models = useModelState((s) => s.loadedSourceNames.size);
  return <StatusItem>{plural(models, "model")} loaded</StatusItem>;
};

const SelectionItem: React.FC = () => {
  const selected = useSelectedObjectStore((s) => selectionCount(s.selectedObjects));
  return <StatusItem>{selected ? `${plural(selected, "object")} selected` : "Nothing selected"}</StatusItem>;
};

const ModeItem: React.FC = () => {
  const {modes, active} = useShellMode();
  const mode = modes.find((m) => m.id === active);
  return mode ? <StatusItem title="Mode">Mode: {mode.label}</StatusItem> : null;
};

const UndoItem: React.FC = () => {
  const lastAction = useViewerHistoryStore((s) => s.past[s.past.length - 1]?.label ?? null);
  return lastAction ? (
    <StatusItem title="What Undo (Ctrl+Z) undoes" muted>
      Undo: {lastAction}
    </StatusItem>
  ) : null;
};

/** The procedural compile, as the status bar words it: null when there is nothing to say. */
export function compileLabel(job: {status: string; error?: string | null} | null | undefined, dirty: boolean): {text: string; tone: "busy" | "bad" | "ok" | "idle"} | null {
  if (job?.status === "queued" || job?.status === "running") return {text: "Compiling…", tone: "busy"};
  if (job?.status === "error") return {text: "Compile failed", tone: "bad"};
  if (job?.status === "done" || job?.status === "cached") return {text: dirty ? "Compiled (edited since)" : "Compiled", tone: dirty ? "idle" : "ok"};
  return null;
}

const CompileItem: React.FC = () => {
  const job = useCellBuilderStore((s) => s.compileJob);
  const dirty = useCellBuilderStore((s) => s.dirty);
  const loaded = useCellBuilderStore((s) => s.active !== null || hasEmbeddedDoc(s));
  const label = loaded ? compileLabel(job, dirty) : null;
  if (!label) return null;
  const tone = {busy: "text-amber-400", bad: "text-[var(--ada-fail,#ef4444)]", ok: "text-[var(--ada-pass,#22c55e)]", idle: ""}[label.tone];
  return (
    <StatusItem title="Procedural compile">
      <span className={tone}>{label.text}</span>
    </StatusItem>
  );
};

const VersionItem: React.FC = () => {
  const v = runtime.adapyVersion();
  return v ? (
    <StatusItem title="Viewer version" muted>
      v{v}
    </StatusItem>
  ) : null;
};

/** Core's status items by id. */
export const CORE_STATUS_ITEMS: Readonly<Record<CoreStatusItemId, StatusBarItem>> = {
  connection: {id: "connection", content: <ConnectionItem />},
  models: {id: "models", content: <ModelsItem />},
  selection: {id: "selection", content: <SelectionItem />},
  mode: {id: "mode", content: <ModeItem />},
  undo: {id: "undo", content: <UndoItem />},
  compile: {id: "compile", content: <CompileItem />},
  version: {id: "version", content: <VersionItem />, end: true},
};

/** The items a list names: core's by id, the shell's own as given; an unknown id is left out, a repeated one kept once. */
export function resolveStatusItems(specs: readonly StatusItemSpec[]): StatusBarItem[] {
  const seen = new Set<string>();
  const out: StatusBarItem[] = [];
  for (const spec of specs) {
    const item = typeof spec === "string" ? (isCoreStatusItem(spec) ? CORE_STATUS_ITEMS[spec] : undefined) : spec;
    if (!item || seen.has(item.id)) continue;
    seen.add(item.id);
    out.push(item);
  }
  return out;
}

export interface StatusBarProps {
  /** What to draw, in order: core's items by id and the shell's own. Default: every core item. */
  items?: readonly StatusItemSpec[];
  /** Replaces the bar's look (default `SHELL_BAR` with the height and type size below). */
  className?: string;
}

const DEFAULT_ITEMS: readonly StatusItemSpec[] = ["connection", "models", "selection", "mode", "compile", "undo", "version"];

/** The status bar: the items in order, those marked `end` after the spare room. */
export const StatusBar: React.FC<StatusBarProps> = ({items = DEFAULT_ITEMS, className}) => {
  const all = resolveStatusItems(items);
  const draw = (item: StatusBarItem) => (
    <React.Fragment key={item.id}>
      {item.content ?? (item.text ? <StatusItem title={item.title} muted={item.muted}>{item.text}</StatusItem> : null)}
    </React.Fragment>
  );
  const start = all.filter((i) => !i.end);
  const end = all.filter((i) => i.end);
  return (
    <div className={className ?? `${SHELL_BAR} h-6 gap-4 border-t px-2 text-[11px]`} role="status" data-status-bar="">
      {start.map(draw)}
      {end.length > 0 && <span className="ml-auto flex items-center gap-4">{end.map(draw)}</span>}
    </div>
  );
};

export default StatusBar;
