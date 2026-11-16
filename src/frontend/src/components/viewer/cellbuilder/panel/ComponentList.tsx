/**
 * The procedural model's components -- cells, lofts, openings, equipment -- as a
 * list or a tree: select one, tick it in or out of the build, retype an opening,
 * delete one.
 *
 * The "View" option (remembered in this browser, `useModelPanelViewStore`) arranges
 * the rows flat, by structure, or by structure and cell -- the arrangement is
 * `buildModelTree` (utils/cellbuilder/modelTree). A group row selects everything
 * under it (Fit selection then frames it, or its ⌖ does both), folds, shows its
 * count, and carries a checkbox: a structure's is the structure's own INCLUDE,
 * a space's cascades over the space and what is placed in it (mixed when they
 * differ). A row's checkbox is the component's INCLUDE in the document -- the same
 * flag the compile, the workbook export and any dimmed/excluded view read
 * (utils/cellbuilder/include) -- so ticking it is an undoable document edit.
 *
 * Shown inside the Build tab's "Cells & equipment" section, and on its own as the
 * `core:component-list` panel (`ComponentListBody`), so a shell can dock the list
 * apart from the builder.
 */

import React from "react";

import {PanelCard} from "@/components/ui/PanelFrame";
import {useSceneActions} from "@/layout/sceneActions";
import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";
import {useModelPanelViewStore} from "@/state/modelPanelViewStore";
import {isGroupIncluded} from "@/utils/cellbuilder/groups";
import {isItemIncluded, type IncludeState} from "@/utils/cellbuilder/include";
import {
  buildModelTree,
  MODEL_VIEWS,
  type GroupNode,
  type IncludeMark,
  type ItemNode,
  type ModelNode,
  type ModelView,
} from "@/utils/cellbuilder/modelTree";

import {inputCls} from "./chrome";

/** How many components the open model has. */
export function useComponentCount(): number {
  return useCellBuilderStore((s) => Object.keys(s.cells).length);
}

/** The "View: …" selector: flat / by structure / by structure and cell. */
export const ModelViewSelect: React.FC<{className?: string}> = ({className = ""}) => {
  const view = useModelPanelViewStore((s) => s.view);
  const setView = useModelPanelViewStore((s) => s.setView);
  return (
    <label className={`flex items-center gap-1 text-gray-400 ${className}`} title="How the components are arranged">
      View:
      <select
        className={`${inputCls} text-[11px]`}
        aria-label="Model view"
        data-model-view=""
        value={view}
        onChange={(e) => setView(e.target.value as ModelView)}
      >
        {MODEL_VIEWS.map((v) => (
          <option key={v.id} value={v.id} title={v.hint}>
            {v.label}
          </option>
        ))}
      </select>
    </label>
  );
};

const KIND_COLOUR: Record<string, string> = {cell: "#3b82f6", loft: "#14b8a6", equipment: "#f97316", opening: "#f97316"};

/** The row's look for its include state: dimmed when it is not built. */
const stateClass = (state: IncludeState) => (state === "included" ? "" : "opacity-50");
const stateTitle = (state: IncludeState) =>
  state === "excluded"
    ? "Left out of the build (INCLUDE = false)"
    : state === "excluded-by-structure"
      ? "Left out of the build with its structure"
      : null;

const includeTitle = (included: boolean) =>
  included
    ? "Included in the build -- untick to leave it out (saved with the model)"
    : "Left out of the build -- tick to build it (saved with the model)";

/** A checkbox showing on / off / mixed, mixed as the native indeterminate dash. */
const MarkCheckbox: React.FC<{
  mark: IncludeMark;
  label: string;
  title: string;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}> = ({mark, label, title, disabled, onChange}) => {
  const ref = React.useRef<HTMLInputElement | null>(null);
  React.useEffect(() => {
    if (ref.current) ref.current.indeterminate = mark === "mixed";
  }, [mark]);
  return (
    <input
      ref={ref}
      type="checkbox"
      className="shrink-0 cursor-pointer disabled:cursor-default"
      aria-label={label}
      aria-checked={mark === "mixed" ? "mixed" : mark === "on"}
      checked={mark === "on"}
      disabled={disabled}
      title={title}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
};

const indent = (depth: number): React.CSSProperties => ({paddingLeft: `${depth * 12 + 2}px`});

const ItemRow: React.FC<{node: ItemNode}> = ({node}) => {
  const c = node.cell;
  const s = useCellBuilderStore();
  const selected = s.selectedCellIds.includes(c.id) || s.selection?.cellId === c.id;
  const included = isItemIncluded(c);
  const loc = c.kind === "equipment" && c.params?.SPACE_LOC === "ROOF" ? "roof" : null;
  const hint = stateTitle(node.state);
  return (
    <div
      data-component-row={c.id}
      data-include-state={node.state}
      className={
        "flex items-center gap-1 border-b border-gray-600/40 pb-0.5 cursor-pointer rounded-sm px-0.5 " +
        (selected ? "bg-blue-900/40" : "hover:bg-gray-700/40")
      }
      style={indent(node.depth)}
      onClick={() => s.setSelection({kind: "cell", cellId: c.id})}
    >
      <MarkCheckbox
        mark={included ? "on" : "off"}
        label={`Include ${c.name} in build`}
        title={c.kind === "loft" ? "A loft member is included or left out in its own row of the workbook" : includeTitle(included)}
        disabled={c.kind === "loft"}
        onChange={(on) => s.setCellsIncluded([c.id], on)}
      />
      <span className={`flex min-w-0 flex-1 items-center gap-1 ${stateClass(node.state)}`} title={hint ?? undefined}>
        <span className="inline-block w-2 h-2 rounded-sm shrink-0" style={{background: KIND_COLOUR[c.kind]}} />
        <span className="truncate" title={`${c.origin.map((v) => v.toFixed(2))} / ${c.size.map((v) => v.toFixed(2))}`}>
          {c.name}
        </span>
        {c.kind === "equipment" && <span className="text-gray-400 truncate">{c.equipmentType ?? "generic"}</span>}
        {loc && <span className="text-gray-500 text-[10px]">{loc}</span>}
      </span>
      {c.kind === "opening" && (
        <select
          className="bg-gray-700 text-gray-100 text-[11px] rounded-sm px-1"
          value={c.subtype ?? "door"}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => s.updateCell(c.id, {subtype: e.target.value as "door" | "window"})}
          title="door: jambs + lintel + threshold (cut to floor); window: jambs + head + sill (punched at its height)"
        >
          <option value="door">door</option>
          <option value="window">window</option>
        </select>
      )}
      <button
        className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
        title="Delete"
        onClick={(e) => {
          e.stopPropagation();
          s.removeCell(c.id);
        }}
      >
        🗑
      </button>
    </div>
  );
};

const GroupRow: React.FC<{node: GroupNode; children: React.ReactNode}> = ({node, children}) => {
  const s = useCellBuilderStore();
  const collapsed = useModelPanelViewStore((v) => Boolean(v.collapsed[node.key]));
  const toggleCollapsed = useModelPanelViewStore((v) => v.toggleCollapsed);
  const actions = useSceneActions();
  const selected =
    node.itemIds.length > 0 &&
    s.selection !== null &&
    s.selectedCellIds.length === node.itemIds.length &&
    node.itemIds.every((id) => s.selectedCellIds.includes(id));
  const select = () => s.selectCells(node.itemIds);
  const kindWord = node.kind === "structure" || node.kind === "none" ? "structure" : node.kind === "cell" ? "space" : "group";
  const onInclude = (on: boolean) => {
    if (node.kind === "structure" && node.group) s.setGroupIncluded(node.group.name, on);
    else s.setCellsIncluded(node.itemIds, on);
  };
  const title =
    node.kind === "structure"
      ? isGroupIncluded(node.group)
        ? "Built -- untick to leave this structure out of the next compile (saved with the model)"
        : "Left out of the build -- tick to build it again (saved with the model)"
      : node.kind === "cell"
        ? "Include this space and everything placed in it (mixed: they differ)"
        : "Include everything in this group (mixed: they differ)";
  const hint = stateTitle(node.state);
  const n = node.itemIds.length;
  return (
    <div data-component-group={node.key} data-group-kind={node.kind} data-include-mark={node.include}>
      <div
        className={
          "flex items-center gap-1 border-b border-gray-600/40 pb-0.5 cursor-pointer rounded-sm px-0.5 " +
          (selected ? "bg-blue-900/40" : "hover:bg-gray-700/40")
        }
        style={indent(node.depth)}
        onClick={select}
      >
        <button
          type="button"
          className="shrink-0 w-3 text-gray-400 text-[10px] leading-none"
          aria-expanded={!collapsed}
          aria-label={`${collapsed ? "Expand" : "Collapse"} ${node.label}`}
          onClick={(e) => {
            e.stopPropagation();
            toggleCollapsed(node.key);
          }}
        >
          <span className={"inline-block transition-transform " + (collapsed ? "" : "rotate-90")}>▸</span>
        </button>
        <MarkCheckbox
          mark={node.include}
          label={`Include ${kindWord} ${node.label} in build`}
          title={title}
          disabled={n === 0 && node.kind !== "structure"}
          onChange={onInclude}
        />
        <span className={`flex min-w-0 flex-1 items-center gap-1 ${stateClass(node.state)}`} title={hint ?? undefined}>
          {node.kind === "cell" && <span className="inline-block w-2 h-2 rounded-sm shrink-0" style={{background: KIND_COLOUR.cell}} />}
          <span className={"truncate " + (node.kind === "structure" || node.kind === "none" ? "font-semibold" : "")}>{node.label}</span>
          <span className="text-gray-400">({n})</span>
        </span>
        <button
          type="button"
          className="ml-auto px-1 rounded-sm hover:bg-gray-500/40 disabled:opacity-40"
          title={`Select and frame the ${n} component${n === 1 ? "" : "s"}`}
          aria-label={`Frame ${node.label}`}
          disabled={n === 0}
          onClick={(e) => {
            e.stopPropagation();
            select();
            actions.fitSelection();
          }}
        >
          ⌖
        </button>
      </div>
      {!collapsed && children}
    </div>
  );
};

const Nodes: React.FC<{nodes: ModelNode[]}> = ({nodes}) => (
  <>
    {nodes.map((n) =>
      n.type === "item" ? (
        <ItemRow key={n.key} node={n} />
      ) : (
        <GroupRow key={n.key} node={n}>
          <Nodes nodes={n.children} />
        </GroupRow>
      ),
    )}
  </>
);

/** The list itself, scrolling inside the height its host gives it (`maxHeightClass`).
 * `showViewSelector` puts the View option above the rows (off when the host's
 * header carries it). */
export const ComponentList: React.FC<{maxHeightClass?: string; showViewSelector?: boolean}> = ({
  maxHeightClass = "max-h-56",
  showViewSelector = true,
}) => {
  const cells = useCellBuilderStore((s) => s.cells);
  const groups = useCellBuilderStore((s) => s.groups);
  const loftMembers = useCellBuilderStore((s) => s.loftMembers);
  const view = useModelPanelViewStore((s) => s.view);
  const nodes = React.useMemo(() => buildModelTree(view, {cells, groups, loftMembers}), [view, cells, groups, loftMembers]);
  const empty = Object.keys(cells).length === 0;
  return (
    <div className="flex min-h-0 flex-col gap-1" data-component-list="" data-model-view-active={view}>
      {showViewSelector && !empty && <ModelViewSelect />}
      <div className={`${maxHeightClass} overflow-y-auto flex flex-col gap-1`}>
        {empty && (
          <p className="italic text-gray-400">
            No cells yet — use + Cell to start, or open a template from the
            storage “＋ New from template” menu.
          </p>
        )}
        <Nodes nodes={nodes} />
      </div>
    </div>
  );
};

/**
 * The `core:component-list` panel's content: a count line and the View option over
 * the list, filling the height its dock gives it. Nothing to list (a note) while no
 * procedural model is open.
 */
export const ComponentListBody: React.FC = () => {
  const loaded = useCellBuilderStore((s) => s.active !== null || hasEmbeddedDoc(s));
  const count = useComponentCount();
  if (!loaded) return <p className="p-2 text-xs italic text-gray-400">No procedural model is open.</p>;
  return (
    <div className="flex h-full min-h-0 flex-col gap-1 p-2 text-xs text-gray-100" data-component-list-panel="">
      <div className="flex items-center gap-2 text-gray-400">
        <span>
          {count} component{count === 1 ? "" : "s"}
        </span>
        <ModelViewSelect className="ml-auto" />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ComponentList maxHeightClass="max-h-none" showViewSelector={false} />
      </div>
    </div>
  );
};

/** The list in the classic UI's card (the menu column), while a model is open and the panel is toggled on. */
export const ComponentListPanel: React.FC = () => (
  <PanelCard variant="surface" className="text-xs pointer-events-auto flex min-w-72 max-w-sm flex-col" heading="Cells & equipment">
    <ComponentListBody />
  </PanelCard>
);

export default ComponentListBody;
