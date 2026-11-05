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
//
// Markup is exactly what the panels drew before, so adopting a frame changes no
// pixel and no DOM node.

import React, {Suspense, createContext, useContext} from "react";

import type {PanelAction, PanelFrameProps} from "@/layout/panel";
import {
  WINDOW_CARD,
  WINDOW_HEADER_BUTTON,
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
    <div ref={ref} className={panelClass(variant, className)} {...rest}>
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

const PanelHeaderActionsContext = createContext<readonly PanelAction[] | null>(null);

/** Offer `actions` to the `PanelHeaderActions` slot of the panel below. */
export const PanelHeaderActionsProvider: React.FC<{actions: readonly PanelAction[]; children: React.ReactNode}> = ({
  actions,
  children,
}) => <PanelHeaderActionsContext.Provider value={actions}>{children}</PanelHeaderActionsContext.Provider>;

/** The host's actions for the panel this header belongs to; none outside a provider. */
export function usePanelHeaderActions(): readonly PanelAction[] {
  return useContext(PanelHeaderActionsContext) ?? [];
}

/** The layout host's actions in a panel's own header; nothing when it offers none. */
export function PanelHeaderActions() {
  const actions = usePanelHeaderActions();
  if (actions.length === 0) return null;
  return (
    <WindowHeaderActions>
      {actions.map((a) => (
        <WindowHeaderButton key={a.id} onClick={a.run} title={a.label} ariaLabel={a.label} disabled={a.disabled}>
          {a.icon ? <a.icon /> : a.label}
        </WindowHeaderButton>
      ))}
    </WindowHeaderActions>
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

/** The glyph of "minimise to the tray". */
export const MinimiseGlyph: React.FC = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M3 12 H13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
  </svg>
);

export interface WindowHeaderButtonProps {
  onClick: () => void;
  title: string;
  ariaLabel: string;
  /** Classes before the button's own, e.g. `ml-auto` to push it to the end of the bar. */
  className?: string;
  disabled?: boolean;
  children: React.ReactNode;
}

/**
 * A title-bar button. It stops the press from reaching the bar, which is the
 * window's drag handle, so a click is never taken for the start of a drag.
 */
export function WindowHeaderButton({onClick, title, ariaLabel, className, disabled, children}: WindowHeaderButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      disabled={disabled}
      className={className ? `${className} ${WINDOW_HEADER_BUTTON}` : WINDOW_HEADER_BUTTON}
      onMouseDown={(e) => e.stopPropagation()}
      onTouchStart={(e) => e.stopPropagation()}
    >
      {children}
    </button>
  );
}

/** A group of title-bar buttons at the end of the bar. */
export const WindowHeaderActions: React.FC<{children: React.ReactNode}> = ({children}) => (
  <div className="ml-auto flex items-center gap-1">{children}</div>
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

/** A floating window's card: title bar over a body that may load lazily. */
export function WindowFrame({title, subtitle, dragHandleClassName, actions, children}: WindowFrameProps) {
  return (
    <div className={WINDOW_CARD}>
      <div className={windowHeaderClass(dragHandleClassName)}>
        <span className="font-medium text-sm truncate">{title}</span>
        {subtitle != null && <span className="hidden sm:inline text-xs text-gray-400 truncate">{subtitle}</span>}
        {actions}
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        <Suspense fallback={<div className="p-4 text-sm text-gray-400">Loading…</div>}>{children}</Suspense>
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
  const buttons = actions.map((a) => (
    <WindowHeaderButton key={a.id} onClick={a.run} title={a.label} ariaLabel={a.label} disabled={a.disabled}>
      {a.icon ? <a.icon /> : a.label}
    </WindowHeaderButton>
  ));
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
      <div className="flex items-center gap-2">
        <h2 className="font-bold">{title}</h2>
        {(buttons.length > 0 || close) && <WindowHeaderActions>{buttons}{close}</WindowHeaderActions>}
      </div>
      {children}
    </PanelCard>
  );
}
