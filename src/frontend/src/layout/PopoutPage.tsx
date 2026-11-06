// A pop-out window (`?window=<id>`, see `./popout`): a browser window of its own
// that holds panels, in a split grid of groups like the main window's (tabs,
// stacks, splits, dividers, drag to dock, group actions), without a 3D view.
// Several of them, on several monitors, form one dashboard with the main window:
// a panel moves between windows by its Move menu ("Move to window", over the
// window bus, `./windows`), and the panels of a window that closes go back to
// the main window.
//
// Its arrangement is its own document (context `window/<id>`), kept in this
// browser under the window's id and its dashboard session (`?session=`, the main
// window's), so a reopened window finds it again. A window opened without a
// session is standalone: a session of its own, no main window to talk to.
// `?popout=<panelId>` adds that panel on load (a new window opened for it).
//
// `AppRoot` mounts it under the viewer provider and, in hosted mode, the
// sign-in gate, without the canvas or the deep-link loading.

import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from "react";
import {useStore} from "zustand";

import {WINDOW_HEADER} from "@/components/ui/panelChrome";
import {MoveGlyph} from "@/components/ui/PanelFrame";
import {activeUiShellId, getUiShell} from "@/plugins/uiShells";

import {useBodyPalette} from "./bodyPalette";
import {registerCorePanels} from "./corePanels";
import type {DropTarget} from "./dockDrag";
import {CLASSIC_PRESET_ID, layoutDocKey, type LayoutDoc, type ZoneDef} from "./layout";
import {createLayoutStore} from "./layoutStore";
import {localLayoutPersistence} from "./localPersistence";
import type {PanelAction, PanelId, PanelMenuItem, RegisteredPanel} from "./panel";
import {POPOUT_PARAM, SESSION_PARAM, WINDOW_PARAM, canPopOut} from "./popout";
import {dockInGridDoc, gridOf, migrateLayoutDoc} from "./reducers";
import {getPanel, setPanelOverrides} from "./registry";
import {setPanelController, type PanelController} from "./reveal";
import {GridOpsProvider, GridView, type GridOps} from "./presets/classicGrid";
import {GRID_GROUP_ATTR, groupLabel, gridNodeOfZone} from "./presets/classicPlace";
import {gridGroups, groupOf, shownGrid, type DockEdge, type Rect} from "./splitTree";
import {MAIN_WINDOW_ID, acceptPanels, type WindowId} from "./windowBus";
import {dockAtWindowEdge, dockInWindow, removeFromWindow, windowContext, windowPanels} from "./windowLayout";
import {followMirror} from "./windowMirror";
import {registerCoreMirror} from "./windowMirrorCore";
import {
  dashboardSession,
  moveToWindowItem,
  onFocusPanel,
  storeWindowLabel,
  storedWindowLabel,
  useMainWindowLive,
  useThisWindow,
  windowBus,
  windowLayoutKey,
} from "./windows";

/** Where a pop-out window's panels say they are hosted. */
export const POPOUT_ZONE = "popout";

const ZONES: readonly ZoneDef[] = [];
const NONE: readonly PanelId[] = [];

const EDGES: readonly [DockEdge, string][] = [
  ["left", "Dock left in this window"],
  ["right", "Dock right in this window"],
  ["top", "Dock top in this window"],
  ["bottom", "Dock bottom in this window"],
];

/** May show in a pop-out window now (registered, and allowed there). */
function allowed(id: PanelId): boolean {
  const p = getPanel(id);
  return !!p && canPopOut(p);
}

/** This window's document and the moves on it, with the window bus told of every change. */
function useWindowDoc(windowId: WindowId) {
  const [store] = useState(() => createLayoutStore());
  const context = windowContext(windowId);
  const key = layoutDocKey(CLASSIC_PRESET_ID, context);
  const hydrated = useStore(store, (s) => s.hydrated);
  const raw = useStore(store, (s) => s.prefs.docs[key]);
  useEffect(() => {
    void store.getState().hydrate(localLayoutPersistence(undefined, windowLayoutKey(windowId)));
  }, [store, windowId]);
  const doc = useMemo(() => migrateLayoutDoc(raw, CLASSIC_PRESET_ID, ZONES, context), [raw, context]);
  const api = useMemo(() => {
    const read = () => store.getState().doc(CLASSIC_PRESET_ID, ZONES, context);
    const apply = (fn: (d: LayoutDoc) => LayoutDoc) => {
      store.getState().update(CLASSIC_PRESET_ID, ZONES, fn, context);
      // At once, not after the render: a panel handed to another window must
      // leave this one's presence before the request for it goes out.
      windowBus().setPanels(windowPanels(read()));
    };
    return {read, apply};
  }, [store, context]);
  return {doc, hydrated, ...api};
}

export default function PopoutPage({windowId, panelId}: {windowId: WindowId; panelId: PanelId | null}) {
  registerCorePanels();
  // The active shell's changes to panels (title, options), as in the main window.
  React.useState(() => setPanelOverrides(getUiShell(activeUiShellId())?.panels ?? null));
  const shell = getUiShell(activeUiShellId());
  // The active shell's look, as in the main window.
  useBodyPalette(shell?.bodyPalette, shell?.panels);
  useEffect(() => {
    const tokens = typeof shell?.themeTokens === "function" ? null : shell?.themeTokens;
    for (const [k, v] of Object.entries(tokens ?? {})) document.documentElement.style.setProperty(k, v);
  }, []);

  const bus = windowBus();
  const {doc, hydrated, read, apply} = useWindowDoc(windowId);
  // The group last used: an arriving panel stacks there.
  const lastGroup = useRef<string | null>(null);

  // The panel this window was opened for, once the saved arrangement is in; then off the URL, so a reload does not add it again.
  useEffect(() => {
    if (!hydrated) return;
    const p = panelId ? getPanel(panelId) : undefined;
    if (p && canPopOut(p)) {
      apply((d) => dockInWindow(d, p.id, lastGroup.current));
      if (!storedWindowLabel(windowId) && !/^w\d+$/.test(windowId)) {
        bus.setLabel(p.title);
        storeWindowLabel(windowId, p.title);
      }
    } else {
      bus.setPanels(windowPanels(read()));
    }
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete(POPOUT_PARAM);
      url.searchParams.set(WINDOW_PARAM, windowId);
      // A window opened without a session is standalone: its own, so a reload (and the windows it opens) keep it.
      url.searchParams.set(SESSION_PARAM, dashboardSession());
      window.history.replaceState(window.history.state, "", url.toString());
    } catch {
      // The URL stays as it was.
    }
  }, [hydrated]);

  // The main window's state that mirrored panels read.
  useEffect(() => {
    registerCoreMirror();
    return followMirror(bus);
  }, [bus]);

  // Every change of the arrangement is this window's presence.
  useEffect(() => {
    if (hydrated) bus.setPanels(windowPanels(doc));
  }, [doc, hydrated]);

  // Panels other windows send here; and the panel another window asks to see.
  useEffect(() => {
    if (!hydrated) return;
    const offAccept = acceptPanels(bus, (id) => {
      if (!allowed(id)) return false;
      apply((d) => dockInWindow(d, id, lastGroup.current));
      return true;
    });
    const offFocus = onFocusPanel((id) => {
      if (windowPanels(read()).includes(id)) apply((d) => dockInWindow(d, id));
    });
    return () => {
      offAccept();
      offFocus();
    };
  }, [hydrated, bus]);

  // `reveal` / `hide` in this window: its own panels here; any other is the main window's to show.
  useLayoutEffect(() => {
    const controller: PanelController = {
      isOpen: (id) => windowPanels(read()).includes(id),
      reveal: (id) => {
        if (windowPanels(read()).includes(id)) {
          apply((d) => dockInWindow(d, id));
          return true;
        }
        bus.post({type: "reveal", to: MAIN_WINDOW_ID, panelId: id});
        return true;
      },
      hide: (id) => {
        apply((d) => removeFromWindow(d, id));
        return true;
      },
    };
    setPanelController(controller);
    return () => setPanelController(null);
  }, [read, apply, bus]);

  const me = useThisWindow();
  useEffect(() => {
    document.title = me.label;
  }, [me.label]);

  const ops = useMemo<GridOps>(() => {
    const moveMenu = (p: RegisteredPanel, d: LayoutDoc): PanelAction => {
      const own = groupOf(gridOf(d), p.id);
      const items: PanelMenuItem[] = [];
      for (const [edge, label] of EDGES) items.push({id: `dock-${edge}`, label, run: () => apply((x) => dockAtWindowEdge(x, p.id, edge))});
      for (const g of gridGroups(gridOf(d))) {
        if (g.id === own?.id) continue;
        items.push({id: `stack-${g.id}`, label: `Stack with ${groupLabel(g.panels)}`, run: () => apply((x) => dockInGridDoc(x, p.id, {node: g.id, edge: "center"}))});
      }
      const from = own?.id ?? null;
      const toWindow = moveToWindowItem(p, {
        remove: () => apply((x) => removeFromWindow(x, p.id)),
        restore: () => apply((x) => dockInWindow(x, p.id, from)),
      });
      if (toWindow) items.push(toWindow);
      return {id: "move", label: "Move panel", icon: MoveGlyph, run: () => {}, menu: items};
    };
    const drop = (id: PanelId, target: DropTarget | null): boolean => {
      const node = target ? gridNodeOfZone(target.zone) : null;
      if (!target || !node || !allowed(id)) return false;
      if (target.index !== null) apply((d) => dockInGridDoc(d, id, {node, edge: "center", index: target.index!}));
      else if (target.edge) apply((d) => dockInGridDoc(d, id, {node, edge: target.edge!}));
      else return false;
      return true;
    };
    return {
      update: apply,
      close: (id) => apply((d) => removeFromWindow(d, id)),
      accepts: (p) => canPopOut(p),
      drop,
      dropOutside: () => {},
      panelActions: (p, d) => [moveMenu(p, d)],
      addable: () => [],
      add: () => {},
      usePinned: () => NONE,
      chrome: () => ({actions: ["collapse"], tabIcons: !!shell?.chrome?.groups?.tabIcons}),
      zone: POPOUT_ZONE,
      wrapBody: (p, body) => (p.capabilities.mirrored ? <MirroredBody>{body}</MirroredBody> : body),
    };
  }, [apply]);

  const frame = useRef<HTMLDivElement>(null);
  const grid = shownGrid(gridOf(doc), allowed, {viewport: false});
  const empty = grid.kind === "viewport";
  return (
    <div className="flex h-[100dvh] w-full flex-col bg-[var(--ada-window-bg,#111827)] text-[var(--ada-panel-text)]" data-popout-window={windowId}>
      <WindowTitleBar label={me.label} onRename={(l) => {
        bus.setLabel(l);
        storeWindowLabel(windowId, l);
      }} />
      <div
        ref={frame}
        className="relative min-h-0 flex-1"
        onPointerDownCapture={(e) => {
          const g = (e.target as Element | null)?.closest?.(`[${GRID_GROUP_ATTR}]`);
          if (g) lastGroup.current = g.getAttribute(GRID_GROUP_ATTR);
        }}
      >
        {!hydrated ? null : empty ? (
          <div className="flex h-full w-full items-center justify-center p-6 text-center text-sm text-gray-400" data-popout-empty="">
            Nothing here yet. Move a panel to this window from its Move panel menu, under Move to window.
          </div>
        ) : (
          <GridOpsProvider ops={ops}>
            <GridView grid={grid} doc={doc} frame={frame} onViewportRect={noRect} />
          </GridOpsProvider>
        )}
      </div>
    </div>
  );
}

const noRect = (_: Rect) => {};

/** A mirrored panel's content: dimmed under a notice while there is no main window to mirror. */
function MirroredBody({children}: {children: React.ReactNode}) {
  const mainLive = useMainWindowLive();
  return (
    <div className="relative h-full w-full" data-mirrored="">
      <div className={mainLive ? "h-full w-full" : "pointer-events-none h-full w-full opacity-40"} aria-hidden={mainLive ? undefined : true}>
        {children}
      </div>
      {!mainLive && (
        <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-sm text-gray-200" role="status" data-main-window-closed-body="">
          Main window closed. This panel shows the main window's model; it updates again when the main window is open.
        </div>
      )}
    </div>
  );
}

/** The window's title bar: its label (renamable), and a note when the main window is gone. */
function WindowTitleBar({label, onRename}: {label: string; onRename(label: string): void}) {
  const mainLive = useMainWindowLive();
  const [editing, setEditing] = useState<string | null>(null);
  const commit = () => {
    if (editing !== null && editing.trim()) onRename(editing.trim());
    setEditing(null);
  };
  return (
    <div className={WINDOW_HEADER}>
      {editing === null ? (
        <span className="truncate text-sm font-medium" onDoubleClick={() => setEditing(label)} title="Double-click to rename">
          {label}
        </span>
      ) : (
        <input
          autoFocus
          aria-label="Window name"
          className="min-w-0 rounded-sm bg-gray-800 px-1 text-sm text-white"
          value={editing}
          onChange={(e) => setEditing(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            else if (e.key === "Escape") setEditing(null);
          }}
        />
      )}
      {!mainLive && (
        <span className="rounded-sm bg-amber-700/60 px-1.5 text-xs text-amber-100" role="status" data-main-window-closed="">
          Main window closed
        </span>
      )}
      <button
        type="button"
        className="ml-auto rounded-sm px-2 py-0.5 text-xs text-[var(--ada-window-header-text,#e5e7eb)] hover:bg-white/10"
        title="Rename this window"
        onClick={() => setEditing(label)}
      >
        Rename
      </button>
    </div>
  );
}
