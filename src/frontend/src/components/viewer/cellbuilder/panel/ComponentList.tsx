/**
 * The procedural model's components -- cells, lofts, openings, equipment -- as a
 * list or a tree: select one, tick it in or out of the build, retype an opening,
 * delete one.
 *
 * The "View" option (remembered in this browser, `useModelPanelViewStore`) arranges
 * the rows flat or as the structure tree -- the arrangement is `buildModelTree`
 * (utils/cellbuilder/modelTree). A group row selects everything under it (Fit
 * selection then frames it, or its ⌖ does both), folds, shows its count, and
 * carries a checkbox: a structure's is the structure's own INCLUDE, a space's
 * cascades over the space and what is placed in it (mixed when they differ). A
 * row's checkbox is the component's INCLUDE in the document -- the same flag the
 * compile, the workbook export and any dimmed/excluded view read
 * (utils/cellbuilder/include) -- so ticking it is an undoable document edit.
 *
 * Every row has the same shape -- fold arrow (or its blank), checkbox, swatch,
 * name -- and indents one `INDENT_STEP` per depth, so a space sits one step in
 * from its structure and the equipment in it one step further. A structure's
 * and a space's swatch is the structure's BLUEPRINT colour, the one the scene
 * draws the space in (utils/cellbuilder/blueprintColours); a legend names the
 * blueprints in use when there is more than one.
 *
 * A plugin's refs (state/cellbuilder/refsSlice.ts) show as rows of their own under
 * the row the tree puts them under: select, tick in or out of the build, rename and
 * delete, each as the ref's plugin allows -- each goes to the plugin, which writes
 * its own rows.
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
import {blueprintColour, blueprintsInUse, structureColour} from "@/utils/cellbuilder/blueprintColours";
import {isGroupIncluded, type CellGroup} from "@/utils/cellbuilder/groups";
import {isItemIncluded, type IncludeState} from "@/utils/cellbuilder/include";
import {
  buildModelTree,
  MODEL_VIEWS,
  type GroupNode,
  type IncludeMark,
  type ItemNode,
  type ModelNode,
  type ModelView,
  type RefNode,
} from "@/utils/cellbuilder/modelTree";
import {refColourCss} from "@/utils/cellbuilder/refColour";

import {inputCls} from "./chrome";

/** How many components the open model has. */
export function useComponentCount(): number {
  return useCellBuilderStore((s) => Object.keys(s.cells).length);
}

/** The "View: …" selector: flat / structure tree. */
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

/** The kind colours of the scene (sceneConstants) as the rows' swatches; a space's
 * swatch is its structure's blueprint colour instead (`structureColour`). */
const KIND_COLOUR: Record<string, string> = {cell: "#3b82f6", loft: "#14b8a6", equipment: "#f97316", opening: "#ef4444"};

/** Pixels per tree level: the structure's name to its space's name, and the
 * space's to the name of a unit placed in it. */
export const INDENT_STEP = 24;

/** The name a blueprint shows under: the engine's display name, else the slug. */
const useBlueprintName = () => {
  const blueprints = useCellBuilderStore((s) => s.blueprints);
  return React.useCallback((slug: string) => blueprints.find((b) => b.slug === slug)?.name ?? slug, [blueprints]);
};

const Swatch: React.FC<{colour: string; title?: string}> = ({colour, title}) => (
  <span className="inline-block w-2 h-2 rounded-sm shrink-0" style={{background: colour}} title={title} data-swatch={colour} />
);

/** The blueprints the structures use, each with its colour -- shown while there
 * is more than one, so the cells' colours can be read. */
export const BlueprintLegend: React.FC<{groups: readonly CellGroup[]; className?: string}> = ({groups, className = ""}) => {
  const nameOf = useBlueprintName();
  const used = blueprintsInUse(groups);
  if (used.length < 2) return null;
  return (
    <div className={`flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-gray-400 ${className}`} data-blueprint-legend="" title="Cells are coloured by their structure's blueprint">
      {used.map((bp) => (
        <span key={bp} className="flex items-center gap-1" data-legend-blueprint={bp}>
          <Swatch colour={blueprintColour(bp)} />
          {nameOf(bp)}
        </span>
      ))}
    </div>
  );
};

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

const indent = (depth: number): React.CSSProperties => ({paddingLeft: `${depth * INDENT_STEP + 2}px`});

/** Where a group row has its fold arrow, an item row has this blank, so the names
 * of a space and of the equipment in it stand one indent step apart. */
const FoldBlank: React.FC = () => <span className="shrink-0 w-3" aria-hidden="true" />;

const ItemRow: React.FC<{node: ItemNode}> = ({node}) => {
  const c = node.cell;
  const s = useCellBuilderStore();
  const selected = s.selectedCellIds.includes(c.id) || s.selection?.cellId === c.id;
  const included = isItemIncluded(c);
  const loc = c.kind === "equipment" && c.params?.SPACE_LOC === "ROOF" ? "roof" : null;
  const hint = stateTitle(node.state);
  const nameOf = useBlueprintName();
  const bp = c.kind === "cell" && c.group ? s.groups.find((g) => g.name === c.group)?.blueprint : undefined;
  const colour = c.kind === "cell" ? structureColour(s.groups, c.group) : KIND_COLOUR[c.kind];
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
      <FoldBlank />
      <MarkCheckbox
        mark={included ? "on" : "off"}
        label={`Include ${c.name} in build`}
        title={c.kind === "loft" ? "A loft member is included or left out in its own row of the workbook" : includeTitle(included)}
        disabled={c.kind === "loft"}
        onChange={(on) => s.setCellsIncluded([c.id], on)}
      />
      <span className={`flex min-w-0 flex-1 items-center gap-1 ${stateClass(node.state)}`} title={hint ?? undefined}>
        <Swatch colour={colour} title={bp ? `Blueprint: ${nameOf(bp)}` : undefined} />
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

/** A plugin's ref: its include checkbox, name (double-click to rename) and delete, as its
 * plugin allows; a click selects it. */
const RefRow: React.FC<{node: RefNode}> = ({node}) => {
  const r = node.ref;
  const s = useCellBuilderStore();
  const selected = s.selection?.kind === "ref" && s.selection.refId === r.id;
  const [renaming, setRenaming] = React.useState(false);
  return (
    <div
      data-ref-row={r.id}
      data-include-state={r.include ? "included" : "excluded"}
      className={
        "flex items-center gap-1 border-b border-gray-600/40 pb-0.5 cursor-pointer rounded-sm px-0.5 " +
        (selected ? "bg-blue-900/40" : "hover:bg-gray-700/40")
      }
      style={indent(node.depth)}
      onClick={() => s.setSelection({kind: "ref", refId: r.id})}
    >
      <FoldBlank />
      <MarkCheckbox
        mark={r.include ? "on" : "off"}
        label={`Include ${r.name} in build`}
        title={includeTitle(r.include)}
        onChange={(on) => s.setRefIncluded(r.id, on)}
      />
      <span className={`flex min-w-0 flex-1 items-center gap-1 ${r.include ? "" : "opacity-50 line-through"}`}>
        <Swatch colour={refColourCss(r)} title={r.kindLabel} />
        {renaming ? (
          <input
            autoFocus
            className={`${inputCls} min-w-0 flex-1`}
            defaultValue={r.name}
            aria-label={`Name of ${r.name}`}
            onClick={(e) => e.stopPropagation()}
            onBlur={(e) => {
              setRenaming(false);
              s.renameRef(r.id, e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") setRenaming(false);
            }}
          />
        ) : (
          <span
            className="truncate"
            title={r.edit.rename ? "Double-click to rename" : undefined}
            onDoubleClick={(e) => {
              if (!r.edit.rename) return;
              e.stopPropagation();
              setRenaming(true);
            }}
          >
            {r.name}
          </span>
        )}
        <span className="text-gray-400 truncate">{r.kindLabel}</span>
      </span>
      {r.edit.remove && (
        <button
          className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
          title="Delete"
          aria-label={`Delete ${r.name}`}
          onClick={(e) => {
            e.stopPropagation();
            s.removeRef(r.id);
          }}
        >
          🗑
        </button>
      )}
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
  const nameOf = useBlueprintName();
  // A structure's swatch and its spaces' are the structure's blueprint colour; the
  // gathering rows ("No structure", "Unassigned") carry none.
  const bp = node.kind === "structure" ? node.group?.blueprint : node.kind === "cell" ? s.groups.find((g) => g.name === node.structure)?.blueprint : undefined;
  const swatch =
    node.kind === "structure" || node.kind === "cell" ? (
      <Swatch colour={structureColour(s.groups, node.structure)} title={bp ? `Blueprint: ${nameOf(bp)}` : undefined} />
    ) : (
      <span className="inline-block w-2 shrink-0" aria-hidden="true" />
    );
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
          {swatch}
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
          {n.refs?.map((r) => <RefRow key={r.key} node={r} />)}
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
  const refMap = useCellBuilderStore((s) => s.refs);
  const view = useModelPanelViewStore((s) => s.view);
  const nodes = React.useMemo(
    () => buildModelTree(view, {cells, groups, loftMembers, refs: Object.values(refMap)}),
    [view, cells, groups, loftMembers, refMap],
  );
  const empty = Object.keys(cells).length === 0;
  return (
    <div className="flex min-h-0 flex-col gap-1" data-component-list="" data-model-view-active={view}>
      {showViewSelector && !empty && <ModelViewSelect />}
      {showViewSelector && !empty && <BlueprintLegend groups={groups} />}
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
  const groups = useCellBuilderStore((s) => s.groups);
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
      <BlueprintLegend groups={groups} />
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
