// The Scene tree's row menu on the loaded FEA result's rows (sets, property
// values; `@/utils/scene/fea/feaTreeRows`), in the main window and in a pop-out
// alike: every item is a scene action (`@/layout/sceneActions`), so a pop-out's
// menu acts on the main window's scene. Pure over the view it is given, so it is
// tested without a DOM.

import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import type {SceneActions} from "@/layout/sceneActions";
import type {FeaTreeView} from "@/state/feaIsolationStore";
import {isFeaTreeRowId, isIsolatableRowId} from "@/utils/scene/fea/feaTreeRows";

import type {TreeNodeData} from "./CustomNode";

/** Does the menu open on FEA rows (and so take the FEA items, not a model's)? */
export const isFeaMenu = (rows: readonly TreeNodeData[]): boolean => rows.length > 0 && rows.every((r) => isFeaTreeRowId(r.id));

/** The items for `rows` (FEA rows), given the view now. */
export function feaRowMenuItems(rows: readonly TreeNodeData[], view: FeaTreeView, actions: Pick<SceneActions, "feaIsolate" | "feaClearIsolation" | "feaToggleContext" | "feaHideRows" | "feaShowRows">): KebabMenuItem[] {
    const ids = rows.map((r) => r.id).filter(isIsolatableRowId);
    const isolated = new Set(view.isolation.rows);
    const what = ids.length > 1 ? ` ${ids.length} rows` : "";
    const none = ids.length === 0 ? "Only element sets and property values can be isolated; nodes carry no faces" : undefined;
    const anyIn = ids.some((id) => isolated.has(id));
    const allIn = ids.length > 0 && ids.every((id) => isolated.has(id));
    return [
        {key: "fea-isolate", label: `Isolate${what}`, onClick: () => actions.feaIsolate(ids, "replace"), disabled: !ids.length, title: none},
        {
            key: "fea-isolate-add",
            label: `Add${what} to the isolation`,
            onClick: () => actions.feaIsolate(ids, "add"),
            disabled: !ids.length || allIn || isolated.size === 0,
            title: none ?? (isolated.size === 0 ? "Nothing is isolated yet" : allIn ? "Already isolated" : undefined),
        },
        {
            key: "fea-isolate-remove",
            label: `Remove${what} from the isolation`,
            onClick: () => actions.feaIsolate(ids, "remove"),
            disabled: !anyIn,
            title: anyIn ? undefined : "Not part of the isolation",
        },
        {
            key: "fea-clear",
            label: "Clear isolation",
            onClick: () => actions.feaClearIsolation(),
            disabled: isolated.size === 0,
            title: isolated.size === 0 ? "Nothing is isolated" : undefined,
        },
        {
            key: "fea-context",
            label: view.isolation.contextWireframe && !view.contextReason ? "Rest as wireframe: on" : "Rest as wireframe: off",
            onClick: () => actions.feaToggleContext(),
            disabled: view.contextReason !== null,
            title: view.contextReason ?? "Draw what the isolation hides as its element edges, or hide it outright",
        },
        {key: "fea-hide", label: `Hide${what}`, onClick: () => actions.feaHideRows(ids), disabled: !ids.length, title: none, separatorBefore: true},
        {key: "fea-show", label: `Unhide${what}`, onClick: () => actions.feaShowRows(ids), disabled: !ids.length, title: none},
    ];
}
