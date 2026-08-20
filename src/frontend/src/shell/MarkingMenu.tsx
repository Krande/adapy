import React from "react";
import {Icon, cn} from "@/components/ui";
import {DIRECTIONS, directionFromDelta, markingItemsFor, type Direction, type MarkingItem} from "./markingMenuItems";
import {useModeStore} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {useTableNavStore} from "@/state/tableNavStore";
import {fitAll, focusSelection, hideSelection, unhideAll} from "./inspectActions";
import {compilePreview, undo} from "./buildActions";
import {copySelectionNames} from "@/utils/clipboard/copySelectionNames";
import {Z} from "./zIndex";

// A Maya-style marking menu on right-click in the viewport.
//
// Why radial rather than a list: in a list you read, then aim, then click. In a marking
// menu the item's DIRECTION is the memory — after a few uses you right-click, flick, and
// release without reading anything, and the menu barely has time to appear. That only
// works if positions are stable, which is why markingMenuItems assigns directions
// explicitly and disables inapplicable entries in place rather than removing them.
//
// Coexistence with the cellbuilder: CellBuilderController already claims right-click for
// its cell and port menus, and signals that by calling preventDefault(). Its listener sits
// on the canvas container, ours on the viewport wrapper, so it runs first and we simply
// yield when it has handled the event. No changes to that controller.

// Wide enough that the E/W labels clear the centre without the wheel feeling loose.
const RADIUS = 96;

interface MenuState {
    x: number;
    y: number;
    items: MarkingItem[];
}

export default function MarkingMenu() {
    const [menu, setMenu] = React.useState<MenuState | null>(null);
    const [hover, setHover] = React.useState<Direction | null>(null);
    const originRef = React.useRef<{x: number; y: number} | null>(null);

    const mode = useModeStore((s) => s.mode);

    React.useEffect(() => {
        // Bound on the window, not on the viewport element. ViewportHost is lazy, so a
        // querySelector at mount time finds nothing and the listener would never attach —
        // which is exactly what happened the first time. Instead we listen globally and
        // check the event's origin, which is also correct if the viewport ever remounts.
        const onContextMenu = (raw: Event) => {
            const e = raw as MouseEvent;
            // The cellbuilder handled it (a cell or a port was hit) — stand down.
            if (e.defaultPrevented) return;
            // Only inside the viewport: a right-click in a dock should still get the
            // browser's own menu (copying a value out of the data table, for instance).
            const target = e.target as HTMLElement | null;
            const host = target?.closest?.("[data-testid='viewport-host']");
            if (!host) return;
            // …but not when something is covering the 3D. Convert mode paints the
            // converter over the canvas, and a radial menu of camera and selection
            // actions on top of a file-conversion form is nonsense — the model it would
            // act on is not even visible.
            if (target?.closest?.("[data-viewport-overlay]")) return;
            e.preventDefault();

            const cb = useCellBuilderStore.getState();
            const sel = useSelectedObjectStore.getState().selectedObjects;
            let selCount = 0;
            sel.forEach((r) => (selCount += r.size));

            const items = markingItemsFor({
                mode,
                // We do not re-pick the scene here: the click has already gone through the
                // ordinary selection path, so "what is selected" is the honest context and
                // avoids a second raycast on every right-click.
                target: selCount > 0 ? "geometry" : "empty",
                hasSelection: selCount > 0 || (cb.active !== null && cb.selection !== null),
                hasEntities:
                    useTreeViewStore.getState().treeData != null ||
                    (cb.active !== null && Object.keys(cb.cells).length > 0),
                feaActive: useFeaAnimationStore.getState().sessionActive,
                builderActive: cb.active !== null,
            });

            originRef.current = {x: e.clientX, y: e.clientY};
            setHover(null);
            setMenu({x: e.clientX, y: e.clientY, items});
        };

        window.addEventListener("contextmenu", onContextMenu);
        return () => window.removeEventListener("contextmenu", onContextMenu);
    }, [mode]);

    // Flick-to-pick: track the pointer while the menu is up and highlight the direction
    // it points. Releasing there runs it — the gesture that makes marking menus fast.
    React.useEffect(() => {
        if (!menu) return;

        const onMove = (e: PointerEvent) => {
            const o = originRef.current;
            if (!o) return;
            setHover(directionFromDelta(e.clientX - o.x, e.clientY - o.y));
        };
        const onUp = (e: PointerEvent) => {
            const o = originRef.current;
            if (!o) return;
            const dir = directionFromDelta(e.clientX - o.x, e.clientY - o.y);
            // Only a genuine flick commits. A plain right-click-release (no travel)
            // leaves the menu up to be clicked normally.
            if (dir) {
                const item = menu.items.find((i) => i.dir === dir);
                if (item && !item.disabledReason) runItem(item.id);
                setMenu(null);
            }
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") setMenu(null);
        };
        const onDown = (e: PointerEvent) => {
            // A click outside the wheel dismisses.
            const o = originRef.current;
            if (o && Math.hypot(e.clientX - o.x, e.clientY - o.y) > RADIUS + 46) setMenu(null);
        };

        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
        window.addEventListener("pointerdown", onDown);
        window.addEventListener("keydown", onKey);
        return () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            window.removeEventListener("pointerdown", onDown);
            window.removeEventListener("keydown", onKey);
        };
    }, [menu]);

    if (!menu) return null;

    return (
        <div style={{zIndex: Z.contextMenu}} className="fixed inset-0" aria-hidden={false}>
            <div
                role="menu"
                aria-label="Viewport actions"
                className="absolute"
                style={{left: menu.x, top: menu.y}}
            >
                {/* Origin dot — the anchor the gesture is measured from. */}
                <span className="absolute -translate-x-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-content-subtle" />

                {menu.items.map((item) => {
                    const angle = (DIRECTIONS.indexOf(item.dir) * 45 - 90) * (Math.PI / 180);
                    const x = Math.cos(angle) * RADIUS;
                    const y = Math.sin(angle) * RADIUS;
                    const active = hover === item.dir && !item.disabledReason;
                    return (
                        <button
                            key={item.id}
                            role="menuitem"
                            type="button"
                            disabled={Boolean(item.disabledReason)}
                            title={item.disabledReason ?? item.label}
                            onPointerDown={(e) => e.stopPropagation()}
                            onClick={() => {
                                if (!item.disabledReason) runItem(item.id);
                                setMenu(null);
                            }}
                            className={cn(
                                "ada-focus absolute flex items-center gap-1.5 px-2 h-7 -translate-x-1/2 -translate-y-1/2",
                                "rounded-md border text-xs font-medium whitespace-nowrap shadow-popover",
                                "transition-colors duration-(--ada-dur-fast)",
                                item.disabledReason
                                    // Dimmed but still READABLE. The whole premise is
                                    // that an item's position is learnable, and you
                                    // cannot learn the position of something you cannot
                                    // see — so a disabled entry stays legible and says
                                    // why it is off, rather than fading to nothing.
                                    ? "bg-surface-2/80 text-content-muted border-edge/60 cursor-default"
                                    : active
                                      ? "bg-accent text-accent-fg border-accent scale-105"
                                      : "bg-surface-1 text-content border-edge pointer-fine:hover:bg-surface-3",
                            )}
                            style={{left: x, top: y}}
                        >
                            <Icon name={item.icon} size="sm" />
                            {item.label}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

/**
 * Run a marking-menu item.
 *
 * Every branch delegates to the same handler the rail, the palette and the keyboard use.
 * A fourth entry point must not become a fourth implementation.
 */
function runItem(id: string): void {
    const mode = useModeStore.getState().mode;
    const layout = useLayoutStore.getState();

    switch (id) {
        case "fit-all":
            return fitAll();
        case "focus-selection":
            return focusSelection();
        case "hide-selection":
            return hideSelection();
        case "unhide-all":
            return unhideAll();
        case "undo":
            return undo();
        case "compile-preview":
            return compilePreview();
        case "copy-names":
            void copySelectionNames(useSelectedObjectStore.getState().selectedObjects);
            return;
        case "show-properties":
            return layout.openPanel(mode, "properties", "right");
        case "section-planes":
            useSceneInfoStore.getState().setMode("section");
            return layout.openPanel(mode, "scene", "right");
        case "show-in-data":
            useTableNavStore.getState().setPanelOpen(true);
            return layout.openPanel(mode, "fea-table", "bottom");
        default:
            console.warn(`[markingMenu] no handler for "${id}"`);
    }
}
