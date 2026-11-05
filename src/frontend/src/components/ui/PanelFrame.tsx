// Panel frames: the chrome around a panel's content.
//
//   * `PanelCard` -- the menu-column card (or its bare surface) that every
//     classic panel is drawn in. It replaces each panel spelling out the chrome
//     class string, and a few panels redefining it locally.
//   * `WindowFrame` -- a floating window's card: a title bar (title, optional
//     subtitle, actions) over a body that loads lazily. The in-viewer
//     admin / convert window and the attribute search window both draw it.
//   * `PanelFrame` -- the frame a layout host puts around a panel whose spec says
//     `frame: "host"`, from the contract's `PanelFrameProps`: a window frame when
//     the host floats the panel, a card otherwise. A UI shell may supply its own.
//   * `PanelHeaderActions` -- the slot in a panel's OWN header (`frame: "own"`)
//     where a layout host's actions (float, dock, minimise) go. It renders
//     nothing until a host provides actions (`PanelHeaderActionsProvider`).
//     `PanelCard` carries it beside its heading.
//   * `WindowHeaderMenuButton` -- an action with a `menu` (move to the grid...).
//   * `WindowDockProvider` -- what a host lets a panel's own window do: its
//     title-bar actions (`WindowFrame`, `WindowDockActions`) and drag-to-dock
//     (`FloatingWindow`).
//
// Markup is exactly what the panels drew before, so adopting a frame changes no
// pixel and no DOM node.

import React, {Suspense, createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState} from "react";
import {createPortal} from "react-dom";

import type {PanelAction, PanelFrameProps} from "@/layout/panel";
import {PANEL_ACTIONS_ATTR, ZONE_ATTR, ZONE_PANEL_ATTR} from "@/layout/zoneKeys";
import {
  WINDOW_CARD,
  WINDOW_HEADER_BUTTON,
  panelBodyAttrs,
  panelClass,
  windowHeaderClass,
  type PanelCardVariant,
} from "./panelChrome";

export interface PanelCardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** `"card"` (default): rounded and padded. `"surface"`: colours, border and shadow only. */
  variant?: PanelCardVariant;
  /** A bold heading drawn as the card's first child. */
  heading?: React.ReactNode;
  ref?: React.Ref<HTMLDivElement>;
}

/** The menu-column card. Extra classes follow the chrome; other props reach the root `div`. */
export function PanelCard({variant = "card", heading, className, children, ref, ...rest}: PanelCardProps) {
  const actions = usePanelHeaderActions();
  const h2 = heading != null && <h2 className="font-bold">{heading}</h2>;
  return (
    <div ref={ref} className={panelClass(variant, className)} {...panelBodyAttrs} {...rest}>
      {h2 && actions.length > 0 ? (
        <div className="flex items-center gap-2">
          {h2}
          <PanelHeaderActions />
        </div>
      ) : (
        h2
      )}
      {children}
    </div>
  );
}

const PanelHeaderActionsContext = createContext<{actions: readonly PanelAction[]; panelId?: string} | null>(null);

/**
 * Offer `actions` to the `PanelHeaderActions` slot of the panel below. With
 * `panelId` the slot names its panel (`PANEL_ACTIONS_ATTR`), so focus can be
 * moved to it.
 */
export const PanelHeaderActionsProvider: React.FC<{actions: readonly PanelAction[]; panelId?: string; children: React.ReactNode}> = ({
  actions,
  panelId,
  children,
}) => {
  const value = useMemo(() => ({actions, panelId}), [actions, panelId]);
  return <PanelHeaderActionsContext.Provider value={value}>{children}</PanelHeaderActionsContext.Provider>;
};

/** The host's actions for the panel this header belongs to; none outside a provider. */
export function usePanelHeaderActions(): readonly PanelAction[] {
  return useContext(PanelHeaderActionsContext)?.actions ?? [];
}

/** The layout host's actions in a panel's own header; nothing when it offers none. */
export function PanelHeaderActions() {
  const panelId = useContext(PanelHeaderActionsContext)?.panelId;
  const actions = usePanelHeaderActions();
  if (actions.length === 0) return null;
  return <WindowHeaderActions panelId={panelId}>{actions.map(actionButton)}</WindowHeaderActions>;
}

/** An action's title-bar button: a menu button when it has a menu. */
function actionButton(a: PanelAction) {
  if (a.menu) return <WindowHeaderMenuButton key={a.id} action={a} />;
  return (
    <WindowHeaderButton key={a.id} onClick={a.run} title={a.label} ariaLabel={a.label} disabled={a.disabled}>
      {a.icon ? <a.icon /> : a.label}
    </WindowHeaderButton>
  );
}

const MENU_ITEM = "menuitem";

/**
 * A title-bar button opening its action's menu, drawn on the page body so no
 * scrolling dock or moved window clips it. Arrows, Home and End move between the
 * items, Esc closes it and returns focus to the button.
 */
export function WindowHeaderMenuButton({action}: {action: PanelAction}) {
  const [at, setAt] = useState<{top: number; right: number} | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const items = () => Array.from(menu.current?.querySelectorAll<HTMLElement>(`[role="${MENU_ITEM}"]:not([disabled])`) ?? []);
  const close = (refocus: boolean) => {
    setAt(null);
    if (refocus) button.current?.focus();
  };
  useLayoutEffect(() => {
    // Above the button when there is no room below it.
    const m = menu.current;
    const r = button.current?.getBoundingClientRect();
    if (!m || !r || r.bottom + 4 + m.offsetHeight <= window.innerHeight) return;
    m.style.top = `${Math.max(4, r.top - 4 - m.offsetHeight)}px`;
  }, [at]);
  useEffect(() => {
    if (!at) return;
    items()[0]?.focus();
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !button.current?.contains(t)) setAt(null);
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [at !== null]);
  const toggle = () => {
    const r = button.current?.getBoundingClientRect();
    setAt(at || !r ? null : {top: r.bottom + 4, right: Math.max(4, window.innerWidth - r.right)});
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    const list = items();
    const i = list.indexOf(document.activeElement as HTMLElement);
    const go = (j: number) => list[(j + list.length) % list.length]?.focus();
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(list.length - 1);
    else if (e.key === "Escape") close(true);
    else if (e.key === "Tab") return close(false);
    else return;
    e.preventDefault();
    e.stopPropagation();
  };
  return (
    <>
      <button
        ref={button}
        type="button"
        onClick={toggle}
        title={action.label}
        aria-label={action.label}
        aria-haspopup="menu"
        aria-expanded={at !== null}
        disabled={action.disabled}
        className={WINDOW_HEADER_BUTTON}
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {action.icon ? <action.icon /> : action.label}
      </button>
      {at &&
        createPortal(
          <div
            ref={menu}
            role="menu"
            aria-label={action.label}
            className="fixed z-[70] min-w-44 rounded-md border border-gray-600 bg-gray-800 py-1 text-sm text-white shadow-lg"
            style={{top: at.top, right: at.right}}
            onKeyDown={onKeyDown}
          >
            {action.menu!.map((item) => (
              <button
                key={item.id}
                type="button"
                role={MENU_ITEM}
                disabled={item.disabled}
                aria-current={item.current || undefined}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-white/10 focus:bg-white/10 focus:outline-none disabled:text-gray-500 disabled:hover:bg-transparent"
                onClick={() => {
                  close(false);
                  item.run();
                }}
              >
                <span className="w-2 text-blue-400" aria-hidden="true">
                  {item.current ? "•" : ""}
                </span>
                {item.label}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}

/** The glyph of a window's close button. */
export const CloseGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
  </svg>
);

/** The glyph of "open in a dedicated tab". */
export const ExternalGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path
      d="M9 2 H14 V7 M14 2 L8 8 M11 9 V13 H3 V5 H7"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      fill="none"
    />
  </svg>
);

/** The glyph of "float in a window". */
export const FloatGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M5 2 H14 V11 H5 Z M2 5 V14 H11" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="none" />
  </svg>
);

/** The glyph of "dock back into the layout". */
export const DockGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M2 2 H14 V14 H2 Z M6 2 V14" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="none" />
  </svg>
);

/** The glyph of "move to another dock". */
export const MoveGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M2 2 H14 V14 H2 Z M10 2 V14 M4 8 H8 M6 6 L8 8 L6 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
  </svg>
);

/** The glyph of "minimise to the tray". */
export const MinimiseGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M3 12 H13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
  </svg>
);

/** The glyph of "pin across modes". */
export const PinGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M6 2 H10 L9.5 6 L12 8.5 H4 L6.5 6 Z M8 8.5 V14" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" fill="none" />
  </svg>
);

/** The glyph of "add a panel here". */
export const AddGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M8 3 V13 M3 8 H13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
  </svg>
);

export interface WindowHeaderButtonProps {
  onClick: () => void;
  title: string;
  ariaLabel: string;
  /** Classes before the button's own, e.g. `ml-auto` to push it to the end of the bar. */
  className?: string;
  disabled?: boolean;
  /** A toggle's state, as `aria-pressed`. */
  pressed?: boolean;
  children: React.ReactNode;
}

/**
 * A title-bar button. It stops the press from reaching the bar, which is the
 * window's drag handle, so a click is never taken for the start of a drag.
 */
export function WindowHeaderButton({onClick, title, ariaLabel, className, disabled, pressed, children}: WindowHeaderButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      disabled={disabled}
      aria-pressed={pressed}
      className={className ? `${className} ${WINDOW_HEADER_BUTTON}` : WINDOW_HEADER_BUTTON}
      onMouseDown={(e) => e.stopPropagation()}
      onTouchStart={(e) => e.stopPropagation()}
    >
      {children}
    </button>
  );
}

/** A group of title-bar buttons at the end of the bar; `panelId` names the panel they act on. */
export const WindowHeaderActions: React.FC<{panelId?: string; children: React.ReactNode}> = ({panelId, children}) => (
  <div className="ml-auto flex items-center gap-1" {...(panelId ? {[PANEL_ACTIONS_ATTR]: panelId} : {})}>
    {children}
  </div>
);

export interface WindowFrameProps {
  title: React.ReactNode;
  /** A muted line after the title, from `sm` up. */
  subtitle?: React.ReactNode;
  /** The floating host's drag-handle class: the title bar carries it while the window can be dragged. */
  dragHandleClassName?: string | null;
  /** Title-bar buttons, after the title. */
  actions?: React.ReactNode;
  children?: React.ReactNode;
}

/**
 * What a layout host lets a panel's OWN window do: its title-bar actions (move
 * to the grid...), and what dragging it by its title bar does. A
 * `FloatingWindow` and a `WindowFrame` inside `WindowDockProvider` pick it up;
 * the window's content does not.
 */
export interface WindowDock {
  panelId: string;
  actions: readonly PanelAction[];
  onDragMove(pointer: {x: number; y: number}): void;
  /** True when the drop was taken (the window is docked). */
  onDragEnd(pointer: {x: number; y: number}): boolean;
}

const WindowDockContext = createContext<WindowDock | null>(null);

export const WindowDockProvider: React.FC<{value: WindowDock | null; children: React.ReactNode}> = ({value, children}) => (
  <WindowDockContext.Provider value={value}>{children}</WindowDockContext.Provider>
);

/** The host's offer to the window this is in; null outside one. */
export function useWindowDock(): WindowDock | null {
  return useContext(WindowDockContext);
}

/** Marks a window's root as a floating zone holding its panel, for the zone keys. */
export function windowDockAttrs(dock: WindowDock | null): Record<string, string> {
  return dock ? {[ZONE_ATTR]: "float", [ZONE_PANEL_ATTR]: dock.panelId} : {};
}

/** The host's actions in a window's own title bar; nothing outside `WindowDockProvider`. */
export function WindowDockActions() {
  const dock = useWindowDock();
  if (!dock || dock.actions.length === 0) return null;
  return <WindowHeaderActions panelId={dock.panelId}>{dock.actions.map(actionButton)}</WindowHeaderActions>;
}

/** A floating window's card: title bar over a body that may load lazily. */
export function WindowFrame({title, subtitle, dragHandleClassName, actions, children}: WindowFrameProps) {
  const dock = useWindowDock();
  const offered = dock && dock.actions.length > 0;
  return (
    <div className={WINDOW_CARD} {...windowDockAttrs(dock)}>
      <div className={windowHeaderClass(dragHandleClassName)}>
        <span className="font-medium text-sm truncate">{title}</span>
        {subtitle != null && <span className="hidden sm:inline text-xs text-gray-400 truncate">{subtitle}</span>}
        {offered ? (
          <div className="ml-auto flex items-center gap-1" {...{[PANEL_ACTIONS_ATTR]: dock.panelId}}>
            {dock.actions.map(actionButton)}
            {actions}
          </div>
        ) : (
          actions
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-hidden" {...panelBodyAttrs}>
        <WindowDockContext.Provider value={null}>
          <Suspense fallback={<div className="p-4 text-sm text-gray-400">Loading…</div>}>{children}</Suspense>
        </WindowDockContext.Provider>
      </div>
    </div>
  );
}

/**
 * The default frame a layout host draws around a `frame: "host"` panel: a window
 * frame in a floating host, a card in a stack, a dock or a sheet. Actions come
 * from the host (float, dock to..., close), so the frame has no opinion on them.
 */
export function PanelFrame({panelId, title, host, actions, onClose, dragHandleClassName, children}: PanelFrameProps) {
  const buttons = actions.map(actionButton);
  const close = onClose && (
    <WindowHeaderButton onClick={onClose} title="Close" ariaLabel="Close panel">
      <CloseGlyph />
    </WindowHeaderButton>
  );
  if (host.kind === "float" || host.kind === "window") {
    return (
      <WindowFrame
        title={title}
        dragHandleClassName={dragHandleClassName}
        actions={
          (buttons.length > 0 || close) && (
            <WindowHeaderActions>
              {buttons}
              {close}
            </WindowHeaderActions>
          )
        }
      >
        {children}
      </WindowFrame>
    );
  }
  return (
    <PanelCard data-panel-id={panelId}>
      <div className={dragHandleClassName ? `flex items-center gap-2 ${dragHandleClassName}` : "flex items-center gap-2"}>
        <h2 className="font-bold">{title}</h2>
        {(buttons.length > 0 || close) && (
          <WindowHeaderActions panelId={dragHandleClassName ? panelId : undefined}>
            {buttons}
            {close}
          </WindowHeaderActions>
        )}
      </div>
      {children}
    </PanelCard>
  );
}
