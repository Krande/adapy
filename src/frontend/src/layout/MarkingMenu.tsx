// The marking menu over the 3D view: a wheel of up to eight commands at the
// pointer, with a short list under it, opened by holding the right button
// (`MarkingGesture`, see `./markingMenuModel` for the gesture and why it is that one)
// or by core's Shift+E. Core mounts one host in the main window's layout
// (`ComposedLayout`), for every UI that has the menu on (`./markingMenuState`);
// a pop-out window has no 3D view and so no marking menu.
//
// Items are commands (`./commands`): their label, icon and chord, greyed in
// place with the command's reason when it cannot run.

import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {OverlayPortal} from "@/components/ui/OverlayPortal";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {VIEWPORT_INPUT_ATTR} from "@/viewport/input";

import {CommandGlyph, ViewIcon} from "./commandIcons";
import {commandChord, getCommand, listCommands, runCommand, type RegisteredCommand} from "./commands";
import {selectionCount} from "./coreCommands";
import {
  MARKING_RADIUS_PX,
  MARKING_VIEWS_SUBMENU,
  MarkingGesture,
  markingGestureOptions,
  resolveMarkingMenu,
  sectorAt,
  sectorOffset,
  stepSelection,
  type MarkingMenuConfig,
  type MarkingMenuContext,
  type MarkingMenuItemDef,
  type MarkingSubmenu,
} from "./markingMenuModel";
import {setMarkingMenuOpener, useServedMarkingMenu} from "./markingMenuState";
import {isPanelOpen, toggle} from "./reveal";
import {activeShellMode} from "./shellModes";
import {bindCoreShortcuts} from "./shortcuts";

/** Core's shortcut that opens the menu from the keyboard (Shift+E). */
export const MARKING_MENU_SHORTCUT = "core:marking-menu";

/** One slot of the wheel as drawn: a command, or a submenu. */
export interface MarkingSlot {
  id: string;
  label: string;
  icon?: React.FC;
  chord?: string;
  /** Why it cannot run now; null when it can. */
  disabledReason: string | null;
  checked?: boolean;
  submenu?: MarkingSubmenu;
}

const isSubmenu = (d: MarkingMenuItemDef): d is MarkingSubmenu => !!d && typeof d === "object" && "items" in d;

/** A command as a slot; null for an id nothing offers now. */
function commandSlot(id: string, byId: ReadonlyMap<string, RegisteredCommand>): MarkingSlot | null {
  const c = byId.get(id);
  if (!c) return null;
  let reason: string | null = null;
  try {
    reason = c.disabledReason?.() ?? null;
  } catch {
    reason = "Not available";
  }
  let checked: boolean | undefined;
  try {
    checked = c.checked?.();
  } catch {
    checked = undefined;
  }
  return {id: c.id, label: c.label, icon: c.icon, chord: commandChord(c), disabledReason: reason, checked};
}

/** The wheel's slots: an id nothing offers is an empty slot (the others keep their places); a submenu with nothing offered too. */
export function markingSlots(defs: readonly MarkingMenuItemDef[], byId: ReadonlyMap<string, RegisteredCommand>): (MarkingSlot | null)[] {
  return defs.map((d, i) => {
    if (d === null) return null;
    if (typeof d === "string") return commandSlot(d, byId);
    if (isSubmenu(d)) {
      const inner = markingSlots(d.items, byId);
      if (!inner.some(Boolean)) return null;
      const runnable = inner.some((s) => s && !s.disabledReason);
      const icon = d.icon ?? (d === MARKING_VIEWS_SUBMENU ? ViewIcon : undefined);
      return {id: `submenu:${i}:${d.label}`, label: d.label, icon, disabledReason: runnable ? null : "Nothing in it can run now", submenu: d};
    }
    return null;
  });
}

/** The list under the wheel: what is offered, in order. */
export function moreSlots(ids: readonly string[], byId: ReadonlyMap<string, RegisteredCommand>): MarkingSlot[] {
  return ids.map((id) => commandSlot(id, byId)).filter((s): s is MarkingSlot => !!s);
}

export interface MarkingMenuViewProps {
  /** The centre, in client px. */
  x: number;
  y: number;
  slots: readonly (MarkingSlot | null)[];
  more: readonly MarkingSlot[];
  /** The highlighted entry: a slot index, then the list's (`slots.length + i`). */
  active: number | null;
  /** A submenu's label, shown at the centre. */
  caption?: string;
  /** Where the pointer is, relative to the centre, while a direction is being chosen. */
  pointer?: {dx: number; dy: number} | null;
  radius?: number;
  onChoose?(index: number): void;
  onHover?(index: number | null): void;
  /** A press on the backdrop (not on an entry), in client px. */
  onBackdrop?(x: number, y: number): void;
}

const PILL =
  "absolute flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 h-7 text-xs shadow-lg " +
  "transition-colors outline-none";

const pillLook = (active: boolean, disabled: boolean) =>
  disabled
    ? // Greyed but readable: a slot's place is what the user learns, so it stays legible and says why.
      "border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-bg,#111827)]/90 text-[var(--ada-panel-text-muted,#9ca3af)] opacity-70 cursor-default"
    : active
      ? "border-[var(--ada-tab-active-text,#ffffff)] bg-[var(--ada-tab-active-bg,#374151)] text-[var(--ada-tab-active-text,#ffffff)]"
      : "border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-bg,#111827)] text-[var(--ada-window-header-text,#f3f4f6)] hover:bg-[var(--ada-tab-active-bg,#374151)]";

/** The wheel and its list, as drawn (no gesture): what the host renders, and what the tests render. */
export function MarkingMenuView({x, y, slots, more, active, caption, pointer, radius = MARKING_RADIUS_PX, onChoose, onHover, onBackdrop}: MarkingMenuViewProps) {
  const n = slots.length;
  const listTop = radius + 34;
  return (
    <OverlayPortal>
      <div
        className="fixed inset-0 z-[95]"
        data-marking-menu=""
        onPointerDown={(e) => {
          if (e.target === e.currentTarget) onBackdrop?.(e.clientX, e.clientY);
        }}
        onContextMenu={(e) => e.preventDefault()}
      >
        <div role="menu" aria-label={caption ? `Marking menu: ${caption}` : "Marking menu"} className="absolute" style={{left: x, top: y}}>
          <svg className="pointer-events-none absolute" style={{left: -radius, top: -radius}} width={radius * 2} height={radius * 2} aria-hidden="true">
            <circle cx={radius} cy={radius} r={10} fill="none" stroke="currentColor" strokeOpacity={0.6} className="text-[var(--ada-window-header-text,#f3f4f6)]" />
            <circle cx={radius} cy={radius} r={2.5} fill="currentColor" className="text-[var(--ada-window-header-text,#f3f4f6)]" />
            {pointer && (
              <line
                x1={radius}
                y1={radius}
                x2={radius + Math.max(-radius, Math.min(radius, pointer.dx))}
                y2={radius + Math.max(-radius, Math.min(radius, pointer.dy))}
                stroke="currentColor"
                strokeOpacity={0.7}
                strokeWidth={2}
                className="text-[var(--ada-tab-active-text,#ffffff)]"
              />
            )}
          </svg>
          {caption && (
            <span className="pointer-events-none absolute -translate-x-1/2 translate-y-3 whitespace-nowrap rounded-sm bg-black/60 px-1.5 text-[10px] text-white">
              {caption}
            </span>
          )}
          {slots.map((s, i) => {
            if (!s) return null;
            const at = sectorOffset(i, n, radius);
            const disabled = !!s.disabledReason;
            const on = active === i;
            return (
              <button
                key={s.id}
                type="button"
                role="menuitem"
                tabIndex={-1}
                data-marking-slot={i}
                aria-disabled={disabled || undefined}
                aria-haspopup={s.submenu ? "menu" : undefined}
                aria-checked={s.checked}
                title={disabled ? `${s.label}: ${s.disabledReason}` : s.chord ? `${s.label} (${s.chord})` : s.label}
                className={`${PILL} ${pillLook(on, disabled)}`}
                style={{left: at.x, top: at.y}}
                onPointerEnter={() => onHover?.(i)}
                onClick={() => onChoose?.(i)}
              >
                <CommandGlyph icon={s.icon} label={s.label} />
                <span>{s.label}</span>
                {s.checked && <span aria-hidden="true">✓</span>}
                {s.submenu && (
                  <span aria-hidden="true" className="text-[var(--ada-panel-text-muted,#9ca3af)]">
                    ›
                  </span>
                )}
              </button>
            );
          })}
          {more.length > 0 && (
            <ul
              role="group"
              aria-label="More"
              className="absolute min-w-40 -translate-x-1/2 rounded-md border border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-bg,#111827)] py-1 text-xs text-[var(--ada-window-header-text,#f3f4f6)] shadow-lg"
              style={{left: 0, top: listTop}}
            >
              {more.map((s, j) => {
                const k = n + j;
                const disabled = !!s.disabledReason;
                return (
                  <li key={s.id} role="none">
                    <button
                      type="button"
                      role="menuitem"
                      tabIndex={-1}
                      data-marking-more={j}
                      aria-disabled={disabled || undefined}
                      title={disabled ? `${s.label}: ${s.disabledReason}` : undefined}
                      className={`flex w-full items-center gap-2 px-2.5 py-1 text-left ${disabled ? "opacity-50" : active === k ? "bg-[var(--ada-tab-active-bg,#374151)]" : "hover:bg-white/10"}`}
                      onPointerEnter={() => onHover?.(k)}
                      onClick={() => onChoose?.(k)}
                    >
                      <CommandGlyph icon={s.icon} label={s.label} />
                      <span className="flex-1">{s.label}</span>
                      {s.chord && <kbd className="text-[var(--ada-panel-text-muted,#9ca3af)]">{s.chord}</kbd>}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </OverlayPortal>
  );
}

/** Keep the wheel (and its list) inside the window. */
export function clampMarkingCentre(
  x: number,
  y: number,
  view: {width: number; height: number},
  radius: number = MARKING_RADIUS_PX,
  listHeight = 0,
): {x: number; y: number} {
  const padX = radius + 80; // the side labels
  const top = radius + 24;
  const bottom = Math.max(radius + 24, radius + 34 + listHeight + 8);
  const clamp = (v: number, lo: number, hi: number) => (lo > hi ? (lo + hi) / 2 : Math.max(lo, Math.min(hi, v)));
  return {x: clamp(x, padX, view.width - padX), y: clamp(y, top, view.height - bottom)};
}

/** Is `target` the 3D view itself (core's input surface), not a HUD, a cover or a panel over it? */
export function isViewTarget(target: EventTarget | null): boolean {
  const el = target as Element | null;
  if (!el || typeof el.closest !== "function") return false;
  return !!el.closest(`[${VIEWPORT_INPUT_ATTR}]`) && !el.closest("[data-viewport-cover]");
}

interface Level {
  x: number;
  y: number;
  slots: (MarkingSlot | null)[];
  more: MarkingSlot[];
  caption?: string;
}

interface OpenMenu {
  levels: Level[];
  /** The right button is still down: releasing chooses. */
  held: boolean;
  active: number | null;
  pointer: {dx: number; dy: number} | null;
}

const LIST_ROW_PX = 26;

/** Run a command offered now: a registered one through `runCommand`, core's panel and mode commands directly. */
function runSlot(id: string, byId: ReadonlyMap<string, RegisteredCommand>): void {
  if (getCommand(id)) runCommand(id);
  else byId.get(id)?.run();
}

/**
 * The marking menu's host: the gesture over the 3D view, the keyboard, and the
 * wheel while it is open. Mounted once in the main window's layout; renders
 * nothing (and listens to nothing) while the served UI has the menu off.
 */
export function MarkingMenuHost() {
  const served = useServedMarkingMenu();
  if (!served.on) return null;
  return <MarkingMenuRuntime config={served.config} />;
}

function MarkingMenuRuntime({config}: {config?: MarkingMenuConfig}) {
  const [menu, setMenu] = useState<OpenMenu | null>(null);
  const menuRef = useRef<OpenMenu | null>(null);
  menuRef.current = menu;
  const byIdRef = useRef<Map<string, RegisteredCommand>>(new Map());
  const gestureOpts = useMemo(() => markingGestureOptions(config?.gesture), [config]);
  const lastViewPointer = useRef<{x: number; y: number} | null>(null);

  const close = useCallback(() => setMenu(null), []);

  /** The wheel for now, centred on (x, y) (clamped into the window). */
  const levelAt = useCallback(
    (x: number, y: number, defs?: {items: readonly MarkingMenuItemDef[]; more: readonly string[]; caption?: string}): Level => {
      const byId = new Map(listCommands({toggle: (id) => void toggle(id), isOpen: isPanelOpen}).map((c) => [c.id, c]));
      byIdRef.current = byId;
      let items = defs?.items;
      let moreIds = defs?.more;
      if (!items) {
        const ctx: MarkingMenuContext = {mode: activeShellMode(), feaResult: useFeaAnimationStore.getState().sessionActive, selectionCount: selectionCount()};
        const r = resolveMarkingMenu(config, ctx);
        items = r.items;
        moreIds = r.more;
      }
      const slots = markingSlots(items, byId);
      const more = moreSlots(moreIds ?? [], byId);
      const view = typeof window !== "undefined" ? {width: window.innerWidth, height: window.innerHeight} : {width: 1e4, height: 1e4};
      const c = clampMarkingCentre(x, y, view, MARKING_RADIUS_PX, more.length * LIST_ROW_PX);
      return {x: c.x, y: c.y, slots, more, caption: defs?.caption};
    },
    [config],
  );

  const open = useCallback(
    (x: number, y: number, held: boolean) => {
      const level = levelAt(x, y);
      if (!level.slots.some(Boolean) && !level.more.length) return false;
      // From the keyboard: the first entry that can run is highlighted, for Enter.
      const filled = [...level.slots, ...level.more].map((s) => !!s && !s.disabledReason);
      setMenu({levels: [level], held, active: held ? null : stepSelection(filled, null, 1), pointer: null});
      return true;
    },
    [levelAt],
  );

  /** Choose entry `index` of the top level: run it, or open its submenu (centred at `at`, else at the slot). */
  const choose = useCallback(
    (index: number | null, at?: {x: number; y: number}) => {
      const m = menuRef.current;
      if (!m || index === null) return close();
      const level = m.levels[m.levels.length - 1];
      const slot = index < level.slots.length ? level.slots[index] : level.more[index - level.slots.length];
      if (!slot) return close();
      if (slot.disabledReason) {
        // A click on a greyed slot keeps the menu (its tooltip says why); a flick onto one ends the gesture.
        if (m.held) close();
        return;
      }
      if (slot.submenu) {
        const off = sectorOffset(index, level.slots.length);
        const c = at ?? {x: level.x + off.x, y: level.y + off.y};
        const sub = levelAt(c.x, c.y, {items: slot.submenu.items, more: [], caption: slot.submenu.label});
        const filled = sub.slots.map((s) => !!s && !s.disabledReason);
        setMenu({levels: [...m.levels, sub], held: m.held, active: m.held ? null : stepSelection(filled, null, 1), pointer: null});
        return;
      }
      close();
      runSlot(slot.id, byIdRef.current);
    },
    [close, levelAt],
  );

  /** The entry under client point (x, y) of the top level: a list row under the pointer, else the sector. */
  const entryAt = useCallback(
    (x: number, y: number, target?: Element | null): number | null => {
      const m = menuRef.current;
      if (!m) return null;
      const level = m.levels[m.levels.length - 1];
      const row = target?.closest?.("[data-marking-more]")?.getAttribute("data-marking-more");
      if (row != null) return level.slots.length + Number(row);
      const s = sectorAt(x - level.x, y - level.y, level.slots.length, gestureOpts.deadZonePx);
      return s;
    },
    [gestureOpts.deadZonePx],
  );

  // The gesture and the keys, on the window (capture: before the camera and the scene's own handlers).
  useEffect(() => {
    let pressTarget: EventTarget | null = null;
    let pressAt = {x: 0, y: 0};
    const gesture = new MarkingGesture({
      holdMs: gestureOpts.holdMs,
      moveTolerancePx: gestureOpts.moveTolerancePx,
      setTimer: (fn, ms) => window.setTimeout(fn, ms),
      clearTimer: (h) => window.clearTimeout(h as number),
      open: (x, y) => {
        if (!open(x, y, true)) return gesture.reset();
        // The press began a camera drag; end it, so the menu's flick does not pan.
        const controls = getViewerRuntime().controls.current as {cancel?: () => void} | null;
        try {
          controls?.cancel?.();
        } catch {
          // An older controls without cancel: the moves are held back below anyway.
        }
      },
    });
    const elementAt = (x: number, y: number) => (typeof document.elementFromPoint === "function" ? document.elementFromPoint(x, y) : null);

    const onDown = (e: PointerEvent) => {
      const eligible =
        e.pointerType === "mouse" &&
        !e.shiftKey &&
        !e.ctrlKey &&
        !e.altKey &&
        !e.metaKey &&
        !menuRef.current &&
        isViewTarget(e.target) &&
        useCellBuilderStore.getState().active === null;
      if (eligible) {
        pressTarget = e.target;
        pressAt = {x: e.clientX, y: e.clientY};
      }
      gesture.down({button: e.button, x: e.clientX, y: e.clientY, eligible});
    };
    const onMove = (e: PointerEvent) => {
      // Where the pointer is over the view (null while it is elsewhere), for opening from the keyboard.
      if (!menuRef.current) lastViewPointer.current = isViewTarget(e.target) ? {x: e.clientX, y: e.clientY} : null;
      if (gesture.move(e.clientX, e.clientY) === "swallow") {
        // Held: the camera must not see these moves.
        e.stopPropagation();
        e.preventDefault();
      }
      const m = menuRef.current;
      if (!m) return;
      const level = m.levels[m.levels.length - 1];
      const dx = e.clientX - level.x;
      const dy = e.clientY - level.y;
      const at = entryAt(e.clientX, e.clientY, elementAt(e.clientX, e.clientY));
      // Held, past a submenu's label: the submenu opens there, and the flick goes on from it.
      if (m.held && at !== null && at < level.slots.length && level.slots[at]?.submenu && !level.slots[at]?.disabledReason && Math.hypot(dx, dy) > MARKING_RADIUS_PX + 18) {
        choose(at, {x: e.clientX, y: e.clientY});
        return;
      }
      if (at !== m.active || m.held) setMenu({...m, active: at, pointer: m.held ? {dx, dy} : null});
    };
    const onUp = (e: PointerEvent) => {
      const r = gesture.up({button: e.button, x: e.clientX, y: e.clientY});
      if (r.kind === "replay") {
        // A quick right click whose `contextmenu` came on the press: the Scene menu, as before.
        const target = pressTarget;
        pressTarget = null;
        target?.dispatchEvent(new MouseEvent("contextmenu", {bubbles: true, cancelable: true, clientX: pressAt.x, clientY: pressAt.y, button: 2}));
        return;
      }
      if (r.kind !== "release") return;
      const m = menuRef.current;
      if (!m) return;
      const at = entryAt(r.x, r.y, elementAt(r.x, r.y));
      // Released without a direction: the menu stays, to be clicked or worked from the keys.
      if (at === null) setMenu({...m, held: false, pointer: null});
      else choose(at);
    };
    const onContextMenu = (e: MouseEvent) => {
      if (gesture.contextMenu() === "swallow") {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    const onKey = (e: KeyboardEvent) => {
      const m = menuRef.current;
      if (!m) return;
      const level = m.levels[m.levels.length - 1];
      const filled = [...level.slots, ...level.more].map((s) => !!s);
      let handled = true;
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight" || e.key === "ArrowDown" || (e.key === "Tab" && !e.shiftKey))
        setMenu({...m, held: false, active: stepSelection(filled, m.active, 1)});
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp" || e.key === "Tab") setMenu({...m, held: false, active: stepSelection(filled, m.active, -1)});
      else if (e.key === "Enter" || e.key === " ") choose(m.active);
      else if (e.key === "Backspace" && m.levels.length > 1) setMenu({...m, levels: m.levels.slice(0, -1), active: null});
      else handled = false;
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    const onCancel = () => {
      gesture.reset();
    };
    const opts = {capture: true};
    window.addEventListener("pointerdown", onDown, opts);
    window.addEventListener("pointermove", onMove, opts);
    window.addEventListener("pointerup", onUp, opts);
    window.addEventListener("pointercancel", onCancel, opts);
    window.addEventListener("contextmenu", onContextMenu, opts);
    window.addEventListener("keydown", onKey, opts);
    window.addEventListener("blur", onCancel);
    return () => {
      gesture.reset();
      window.removeEventListener("pointerdown", onDown, opts);
      window.removeEventListener("pointermove", onMove, opts);
      window.removeEventListener("pointerup", onUp, opts);
      window.removeEventListener("pointercancel", onCancel, opts);
      window.removeEventListener("contextmenu", onContextMenu, opts);
      window.removeEventListener("keydown", onKey, opts);
      window.removeEventListener("blur", onCancel);
    };
  }, [gestureOpts, open, choose, entryAt, close]);

  // Opening from the keyboard (Shift+E) and from the command: at the pointer over the view, else the view's centre.
  useEffect(() => {
    const openHere = (at?: {x: number; y: number}) => {
      if (menuRef.current) return true;
      let p = at ?? lastViewPointer.current;
      if (!p) {
        const view = document.querySelector(`[${VIEWPORT_INPUT_ATTR}]`)?.getBoundingClientRect();
        if (!view || !view.width) return false;
        p = {x: view.left + view.width / 2, y: view.top + view.height / 2};
      }
      return open(p.x, p.y, false);
    };
    const undoOpener = setMarkingMenuOpener(openHere);
    const unbind = bindCoreShortcuts({[MARKING_MENU_SHORTCUT]: {run: () => openHere()}});
    return () => {
      unbind();
      undoOpener();
    };
  }, [open]);

  // The highlighted entry has the focus while the keys work the menu (a screen reader reads it);
  // where the focus was comes back when the menu closes.
  const returnFocus = useRef<HTMLElement | null>(null);
  const isOpen = !!menu;
  useEffect(() => {
    if (!isOpen) return;
    returnFocus.current = (document.activeElement as HTMLElement | null) ?? null;
    return () => {
      const el = returnFocus.current;
      returnFocus.current = null;
      if (el?.isConnected) el.focus?.({preventScroll: true});
    };
  }, [isOpen]);
  const activeIndex = menu && !menu.held ? menu.active : null;
  const slotCount = menu ? menu.levels[menu.levels.length - 1].slots.length : 0;
  useEffect(() => {
    if (activeIndex === null) return;
    const sel = activeIndex < slotCount ? `[data-marking-slot="${activeIndex}"]` : `[data-marking-more="${activeIndex - slotCount}"]`;
    document.querySelector<HTMLElement>(`[data-marking-menu] ${sel}`)?.focus({preventScroll: true});
  }, [activeIndex, slotCount, menu?.levels.length]);

  if (!menu) return null;
  const level = menu.levels[menu.levels.length - 1];
  return (
    <MarkingMenuView
      x={level.x}
      y={level.y}
      slots={level.slots}
      more={level.more}
      active={menu.active}
      caption={level.caption}
      pointer={menu.pointer}
      onChoose={(i) => choose(i)}
      onHover={(i) => {
        const m = menuRef.current;
        if (m && !m.held && m.active !== i) setMenu({...m, active: i});
      }}
      onBackdrop={(x, y) => {
        // A click in a sector chooses it, as a click on its label does; in the centre or far off, it closes.
        const dist = Math.hypot(x - level.x, y - level.y);
        if (dist > MARKING_RADIUS_PX * 2.2) return close();
        choose(entryAt(x, y, null));
      }}
    />
  );
}
