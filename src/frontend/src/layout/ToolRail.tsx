// Core's tool rail (`UiShellChrome.rail: {tools}`): a column of icon buttons
// down the left of a composed shell's layout, outside its docks, each one a
// command -- its icon, its label and chord as the tooltip, pressed while a
// toggle is on, greyed with the reason while it cannot run. The user arranges
// it: right-click a tool to remove it, or open the customise dialog (the
// rail's last button, its menu, Window -> Customise tool rail…) to add any
// command, reorder, put lines between groups or go back to the shell's own.
// Their rail is kept per shell in the layout prefs (`./toolRailState`), so it
// follows them to the server like their arrangements. The rules are `./toolRailModel`.
//
// Main window only, like the 3D view: a pop-out window draws no shell chrome.

import React, {useEffect, useMemo, useRef, useState, useSyncExternalStore} from "react";

import {OverlayPortal} from "@/components/ui/OverlayPortal";
import {subscribeSelection} from "@/viewport/api/viewportApi";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";

import {MenuList, type MenuItemDef} from "./CommandPalette";
import {CommandGlyph, CustomiseIcon} from "./commandIcons";
import {commandChord, commandsVersion, getCommand, listCommands, matchCommand, runCommand, subscribeCommands, type RegisteredCommand} from "./commands";
import {layoutStore} from "./layoutStore";
import {isPanelOpen, toggle} from "./reveal";
import {shellModesVersion, subscribeShellModes} from "./shellModes";
import {RAIL_SEPARATOR, addRailTool, isRailSeparator, moveRailTool, railIsDefault, removeRailTool, tidyRail, type ToolRailConfig} from "./toolRailModel";
import {closeRailCustomise, openRailCustomise, resetRail, saveRail, savedRail, useRailCustomiseOpen, useRailList} from "./toolRailState";

/** Every command offered now, by id, re-read when commands or modes change. */
function useCommandMap(): Map<string, RegisteredCommand> {
  const v = useSyncExternalStore(subscribeCommands, commandsVersion, commandsVersion);
  const m = useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => new Map(listCommands({toggle: (id) => void toggle(id), isOpen: isPanelOpen}).map((c) => [c.id, c])), [v, m]);
}

const safe = <T,>(f: (() => T) | undefined, fallback: T): T => {
  try {
    return f ? f() : fallback;
  } catch {
    return fallback;
  }
};

/** What a tool shows now: greyed (and why) or not, pressed or not. */
function toolState(c: RegisteredCommand): {reason: string | null; checked: boolean | undefined} {
  return {reason: safe(c.disabledReason, null), checked: safe(c.checked, undefined)};
}

/**
 * Re-render when the state the tools show may have changed: what greys a
 * command or ticks a toggle lives in stores of every kind, so the rail asks
 * its tools again after a click or a key anywhere, when the selection, the
 * history or the layout changes, and once a second, and re-renders only when
 * an answer changed.
 */
function useToolStates(ids: readonly string[], byId: ReadonlyMap<string, RegisteredCommand>): void {
  const [, setSig] = useState("");
  const read = useRef<() => string>(() => "");
  read.current = () =>
    ids
      .map((id) => {
        const c = byId.get(id);
        if (!c) return "";
        const s = toolState(c);
        return `${s.reason ?? ""}|${s.checked ?? ""}`;
      })
      .join("\u0000");
  useEffect(() => {
    const check = () => setSig(read.current());
    check();
    const later = () => window.setTimeout(check, 0);
    window.addEventListener("pointerup", later, true);
    window.addEventListener("keyup", later, true);
    const subs = [layoutStore.subscribe(check), subscribeSelection(check), useViewerHistoryStore.subscribe(check)];
    const tick = window.setInterval(check, 1000);
    return () => {
      window.removeEventListener("pointerup", later, true);
      window.removeEventListener("keyup", later, true);
      for (const off of subs) off();
      window.clearInterval(tick);
    };
  }, []);
}

/** Run a command offered now: a registered one through `runCommand`, core's panel and mode commands directly. */
function runTool(c: RegisteredCommand): void {
  if (getCommand(c.id)) runCommand(c.id);
  else if (!safe(c.disabledReason, null)) c.run();
}

const RAIL =
  "flex h-full w-10 flex-col items-center gap-0.5 overflow-y-auto border-r border-[var(--ada-window-border,#374151)] " +
  "bg-[var(--ada-window-header-bg,#1f2937)] py-1 text-[var(--ada-window-header-text,#f3f4f6)]";

const toolButton = (pressed: boolean, disabled: boolean) =>
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-sm transition-colors outline-none focus-visible:ring-1 focus-visible:ring-[var(--ada-tab-active-text,#ffffff)] " +
  (disabled ? "opacity-40 cursor-default" : pressed ? "bg-[var(--ada-tab-active-bg,#374151)] text-[var(--ada-tab-active-text,#ffffff)]" : "hover:bg-white/10");

export interface ToolRailProps {
  /** The shell the user's rail is kept under. */
  shell: string;
  config: ToolRailConfig;
}

/** The rail as drawn: the user's tools that are offered now, lines between groups, the customise button. */
export function ToolRail({shell, config}: ToolRailProps) {
  const list = useRailList(shell, config.tools);
  const byId = useCommandMap();
  const shown = useMemo(() => tidyRail(list, (id) => byId.has(id)), [list, byId]);
  useToolStates(shown, byId);
  const [menuAt, setMenuAt] = useState<{x: number; y: number; tool: string | null} | null>(null);
  const customisable = config.customisable !== false;

  return (
    <nav
      aria-label="Tools"
      data-tool-rail=""
      className={RAIL}
      onContextMenu={(e) => {
        e.preventDefault();
        if (!customisable) return;
        const tool = (e.target as HTMLElement).closest?.("[data-rail-tool]")?.getAttribute("data-rail-tool") ?? null;
        setMenuAt({x: e.clientX, y: e.clientY, tool});
      }}
    >
      {shown.map((id, i) => {
        if (isRailSeparator(id)) return <span key={`sep${i}`} role="separator" className="my-1 h-px w-6 shrink-0 bg-[var(--ada-window-border,#374151)]" />;
        const c = byId.get(id)!;
        const {reason, checked} = toolState(c);
        const chord = commandChord(c);
        return (
          <button
            key={id}
            type="button"
            data-rail-tool={id}
            aria-label={c.label}
            aria-pressed={checked === undefined ? undefined : checked}
            aria-disabled={reason ? true : undefined}
            title={reason ? `${c.label}: ${reason}` : chord ? `${c.label} (${chord})` : c.label}
            className={toolButton(!!checked, !!reason)}
            onClick={() => {
              if (!reason) runTool(c);
            }}
          >
            <CommandGlyph icon={c.icon} label={c.label} />
          </button>
        );
      })}
      {customisable && (
        <button
          type="button"
          aria-label="Customise tool rail"
          title="Customise tool rail…"
          className={`${toolButton(false, false)} mt-auto opacity-70`}
          onClick={openRailCustomise}
        >
          <CustomiseIcon />
        </button>
      )}
      {menuAt && <RailMenu at={menuAt} shell={shell} config={config} list={list} close={() => setMenuAt(null)} />}
    </nav>
  );
}

/** The rail's right-click menu: remove the tool under the pointer, customise, reset. */
function RailMenu({at, shell, config, list, close}: {at: {x: number; y: number; tool: string | null}; shell: string; config: ToolRailConfig; list: string[]; close(): void}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) close();
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
      }
    };
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", esc, true);
    return () => {
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("keydown", esc, true);
    };
  }, [close]);
  const items: MenuItemDef[] = [];
  if (at.tool) {
    const i = list.indexOf(at.tool);
    items.push({label: "Remove from the rail", run: () => saveRail(shell, config.tools, removeRailTool(list, i)), disabled: i < 0});
    items.push({separator: true});
  }
  items.push({label: "Customise tool rail…", run: openRailCustomise});
  items.push({label: "Reset tool rail", run: () => resetRail(shell), disabled: railIsDefault(config.tools, savedRail(shell))});
  return (
    <OverlayPortal>
      <div ref={ref} className="fixed z-[90]" style={{left: at.x, top: at.y}}>
        <MenuList label="Tool rail" items={items} byId={new Map()} close={close} className="relative" />
      </div>
    </OverlayPortal>
  );
}

/** The customise dialog's host: the dialog while it is open (Window -> Customise tool rail…, the rail's button). */
export function ToolRailCustomiseHost(props: ToolRailProps) {
  const open = useRailCustomiseOpen();
  useEffect(() => closeRailCustomise, []);
  if (!open) return null;
  return <ToolRailCustomiseDialog {...props} onClose={closeRailCustomise} />;
}

const DIALOG_BUTTON =
  "rounded-sm border border-[var(--ada-window-border,#374151)] px-2 py-0.5 text-xs hover:bg-white/10 disabled:opacity-40 disabled:hover:bg-transparent";
const ROW_BUTTON = "inline-flex h-6 w-6 items-center justify-center rounded-sm text-xs hover:bg-white/10 disabled:opacity-30 disabled:hover:bg-transparent";

/** The customise dialog: the rail's tools (reorder, remove, lines between groups) and every command to add. */
export function ToolRailCustomiseDialog({shell, config, onClose}: ToolRailProps & {onClose(): void}) {
  const list = useRailList(shell, config.tools);
  const byId = useCommandMap();
  const [query, setQuery] = useState("");
  const [at, setAt] = useState<number | null>(null);
  const save = (next: string[]) => saveRail(shell, config.tools, next);
  const isDefault = railIsDefault(config.tools, savedRail(shell));
  const available = useMemo(
    () =>
      [...byId.values()]
        .filter((c) => !list.includes(c.id))
        .map((c) => ({c, rank: matchCommand(query, `${c.group ?? ""} ${c.label}`) ?? matchCommand(query, c.label)}))
        .filter((x): x is {c: RegisteredCommand; rank: number} => x.rank !== null)
        .sort((a, b) => (query ? a.rank - b.rank : 0) || (a.c.group ?? "").localeCompare(b.c.group ?? "") || a.c.label.localeCompare(b.c.label))
        .map((x) => x.c),
    [byId, list, query],
  );
  /** Add after the chosen row (the end when none is chosen), and choose what was added. */
  const add = (id: string) => {
    const where = at === null ? list.length : at + 1;
    save(addRailTool(list, id, where));
    setAt(where);
  };
  const move = (i: number, to: number) => {
    if (to < 0 || to >= list.length) return;
    save(moveRailTool(list, i, to));
    setAt(to);
  };
  return (
    <OverlayPortal>
      <div className="fixed inset-0 z-[90] flex items-start justify-center bg-black/50 p-4 pt-[10vh]" onClick={onClose}>
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Customise tool rail"
          data-rail-customise=""
          className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-md border border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-bg,#111827)] text-sm text-[var(--ada-panel-text,#f3f4f6)] shadow-2xl"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              onClose();
            }
          }}
        >
          <div className="border-b border-[var(--ada-window-border,#374151)] px-3 py-2 font-medium">Customise tool rail</div>
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-3 overflow-hidden p-3">
            <section aria-label="On the rail" className="flex min-h-0 flex-col">
              <div className="mb-1 flex items-center justify-between text-xs text-[var(--ada-panel-text-muted,#9ca3af)]">
                <span>On the rail</span>
                <button type="button" className={DIALOG_BUTTON} onClick={() => add(RAIL_SEPARATOR)}>
                  Add separator
                </button>
              </div>
              <ol className="min-h-0 flex-1 overflow-y-auto rounded-sm border border-[var(--ada-window-border,#374151)]" data-rail-list="">
                {list.length === 0 && <li className="px-2 py-1.5 text-xs text-[var(--ada-panel-text-muted,#9ca3af)]">No tools. Add some from the list.</li>}
                {list.map((id, i) => {
                  const sep = isRailSeparator(id);
                  const c = sep ? undefined : byId.get(id);
                  const label = sep ? "Separator" : (c?.label ?? id);
                  return (
                    <li
                      key={`${id}:${i}`}
                      data-rail-row={id}
                      aria-selected={at === i}
                      className={`flex cursor-pointer items-center gap-2 px-2 py-0.5 ${at === i ? "bg-[var(--ada-tab-active-bg,#374151)]" : "hover:bg-white/5"}`}
                      onClick={() => setAt(i)}
                    >
                      <span className="inline-flex w-4 justify-center">{sep ? <span className="h-px w-4 bg-current opacity-50" /> : <CommandGlyph icon={c?.icon} label={label} />}</span>
                      <span className={`flex-1 truncate ${sep ? "text-xs italic opacity-70" : ""}`} title={!sep && !c ? "Not offered in this UI now" : undefined}>
                        {label}
                        {!sep && !c && <span className="ml-1 text-xs opacity-60">(not offered now)</span>}
                      </span>
                      <button type="button" aria-label={`Move ${label} up`} title="Move up" className={ROW_BUTTON} disabled={i === 0} onClick={(e) => (e.stopPropagation(), move(i, i - 1))}>
                        ↑
                      </button>
                      <button type="button" aria-label={`Move ${label} down`} title="Move down" className={ROW_BUTTON} disabled={i === list.length - 1} onClick={(e) => (e.stopPropagation(), move(i, i + 1))}>
                        ↓
                      </button>
                      <button
                        type="button"
                        aria-label={`Remove ${label}`}
                        title="Remove from the rail"
                        className={ROW_BUTTON}
                        onClick={(e) => {
                          e.stopPropagation();
                          save(removeRailTool(list, i));
                          setAt(null);
                        }}
                      >
                        ✕
                      </button>
                    </li>
                  );
                })}
              </ol>
            </section>
            <section aria-label="Commands" className="flex min-h-0 flex-col">
              <input
                value={query}
                placeholder="Find a command"
                aria-label="Find a command"
                className="mb-1 rounded-sm border border-[var(--ada-window-border,#374151)] bg-transparent px-2 py-1 text-xs outline-none"
                onChange={(e) => setQuery(e.target.value)}
              />
              <ul className="min-h-0 flex-1 overflow-y-auto rounded-sm border border-[var(--ada-window-border,#374151)]" data-rail-available="">
                {available.length === 0 && <li className="px-2 py-1.5 text-xs text-[var(--ada-panel-text-muted,#9ca3af)]">No command matches.</li>}
                {available.map((c) => (
                  <li key={c.id} className="flex items-center gap-2 px-2 py-0.5 hover:bg-white/5">
                    <span className="inline-flex w-4 justify-center">
                      <CommandGlyph icon={c.icon} label={c.label} />
                    </span>
                    <span className="w-16 shrink-0 truncate text-xs text-[var(--ada-panel-text-muted,#9ca3af)]">{c.group ?? "Commands"}</span>
                    <span className="flex-1 truncate">{c.label}</span>
                    <button type="button" aria-label={`Add ${c.label}`} data-rail-add={c.id} className={DIALOG_BUTTON} onClick={() => add(c.id)}>
                      Add
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          </div>
          <div className="flex items-center justify-between border-t border-[var(--ada-window-border,#374151)] px-3 py-2">
            <button
              type="button"
              className={DIALOG_BUTTON}
              disabled={isDefault}
              onClick={() => {
                resetRail(shell);
                setAt(null);
              }}
            >
              Reset to default
            </button>
            <span className="text-xs text-[var(--ada-panel-text-muted,#9ca3af)]">{"Saved as you go, for this UI."}</span>
            <button type="button" className={DIALOG_BUTTON} onClick={onClose} autoFocus>
              Done
            </button>
          </div>
        </div>
      </div>
    </OverlayPortal>
  );
}
