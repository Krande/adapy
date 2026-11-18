// The builder's three stacked sections of the Selected Object panel, each its own property
// provider (ObjectInfoBoxComponent's CORE_PROPERTY_PROVIDERS), so a plugin's section slots
// between them by order:
//   70 the selected item -- a cell's editors and gizmo toggles (SelectionSection), or a
//      plugin's ref: its name, kind, box (when its plugin lets it move or resize) and include;
//   72 the space it is in: name, box, include, and a link that selects the space;
//   74 the structure it is in (or the active one, with nothing selected): name, blueprint,
//      include and colour.

import React from "react";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import type { BuilderRef } from "@/state/cellbuilder/refsSlice";
import { selectedRef, selectionPlace } from "@/utils/cellbuilder/boxTarget";
import { isGroupIncluded } from "@/utils/cellbuilder/groups";
import { isItemIncluded } from "@/utils/cellbuilder/include";
import type { Vec3 } from "@/utils/cellbuilder/snap";
import { GroupBlueprintSelect, GroupColourInput, renameStructure } from "@/components/viewer/cellbuilder/panel/GroupsSection";
import { SelectionSection } from "./SelectionSection";
import { inputCls } from "./fields";

const frame = "mt-3 border-t border-gray-500/60 pt-2 text-xs text-white";

/** The structure and the space of the builder's pick (`selectionPlace`); with nothing picked,
 *  the active structure. */
function useBuilderPlace(): { structure?: string; space?: string } {
  const selection = useCellBuilderStore((s) => s.selection);
  const cells = useCellBuilderStore((s) => s.cells);
  const refs = useCellBuilderStore((s) => s.refs);
  const groups = useCellBuilderStore((s) => s.groups);
  const loftMembers = useCellBuilderStore((s) => s.loftMembers);
  const activeGroup = useCellBuilderStore((s) => s.activeGroup);
  return React.useMemo(() => {
    if (selection) return selectionPlace(selection, { cells, refs, groups, loftMembers });
    return activeGroup && groups.some((g) => g.name === activeGroup) ? { structure: activeGroup } : {};
  }, [selection, cells, refs, groups, loftMembers, activeGroup]);
}

/** A text field that commits on blur or Enter. */
const NameField: React.FC<{ value: string; label: string; onCommit: (v: string) => void; disabled?: boolean; title?: string }> = ({
  value,
  label,
  onCommit,
  disabled,
  title,
}) => (
  <label className="flex items-center gap-1">
    <span className="text-gray-300">name</span>
    <input
      type="text"
      className={`${inputCls} flex-1 min-w-0`}
      defaultValue={value}
      key={value}
      aria-label={label}
      disabled={disabled}
      title={title}
      onBlur={(e) => onCommit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
      }}
    />
  </label>
);

const boxLine = (b: { origin: readonly number[]; size: readonly number[] }) =>
  `origin ${b.origin.map((v) => v.toFixed(2)).join(", ")} · size ${b.size.map((v) => v.toFixed(2)).join(", ")}`;

/** Three numbers of a box, each written on change. */
const VecFields: React.FC<{ label: string; value: Vec3; min?: number; onChange: (v: Vec3) => void }> = ({ label, value, min, onChange }) => (
  <div className="flex items-center gap-1">
    <span className="text-gray-300 w-10">{label}</span>
    {value.map((v, i) => (
      <input
        key={i}
        type="number"
        step={0.1}
        min={min}
        className={`${inputCls} w-16`}
        value={Number(v.toFixed(3))}
        aria-label={`${label} ${"XYZ"[i]}`}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (!Number.isFinite(n) || (min !== undefined && n < min)) return;
          const next = [...value] as Vec3;
          next[i] = n;
          onChange(next);
        }}
      />
    ))}
  </div>
);

/** A plugin's ref: what the builder knows of it, editable as its plugin allows. The plugin adds
 *  its own form for the rest (a section of its own, matched on `refOwner`). */
const RefItem: React.FC<{ ref_: BuilderRef }> = ({ ref_: r }) => {
  const s = useCellBuilderStore();
  const gizmos = [
    ...(r.edit.move ? ([["translate", "Move"]] as const) : []),
    ...(r.edit.resize ? ([["resize", "Resize"]] as const) : []),
  ];
  return (
    <div className="flex flex-col gap-1.5 px-1 pt-1" data-builder-ref={r.id}>
      <div className="flex items-center gap-1">
        <span className="font-semibold truncate">{r.name}</span>
        <span className="text-gray-400 truncate">{r.kindLabel}</span>
        <span
          className="ml-auto px-1 rounded-sm hover:bg-gray-500/40 cursor-pointer"
          title="Clear selection (Esc)"
          onClick={() => s.setSelection(null)}
        >
          ✕
        </span>
      </div>
      {gizmos.length > 0 && (
        <div className="flex items-center gap-1" title="Direct-manipulation gizmo for this item">
          <span className="text-gray-300">gizmo</span>
          {[...gizmos, ["none", "Off"] as const].map(([m, label]) => (
            <button
              key={m}
              className={"px-1.5 py-0.5 rounded-sm " + (s.gizmoMode === m ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300 hover:bg-gray-600")}
              onClick={() => s.setGizmoMode(m)}
              aria-pressed={s.gizmoMode === m}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {r.edit.rename && <NameField value={r.name} label={`Name of ${r.name}`} onCommit={(v) => s.renameRef(r.id, v)} />}
      {r.edit.move ? (
        <VecFields label="origin" value={r.origin} onChange={(origin) => s.writeRef(r.id, { origin, size: r.size })} />
      ) : null}
      {r.edit.resize ? (
        <VecFields label="size" value={r.size} min={0} onChange={(size) => s.writeRef(r.id, { origin: r.origin, size })} />
      ) : null}
      {!r.edit.move && !r.edit.resize && <div className="text-gray-400">{boxLine(r)}</div>}
      <label className="flex items-center gap-1" title="Whether the build takes it">
        <input type="checkbox" checked={r.include} onChange={(e) => s.setRefIncluded(r.id, e.target.checked)} />
        Include in build
      </label>
      {r.edit.remove && (
        <button className="self-start px-2 py-0.5 rounded-sm bg-gray-700 text-gray-200 hover:bg-gray-600" onClick={() => s.removeRef(r.id)}>
          Delete
        </button>
      )}
    </div>
  );
};

/** Order 70: the selected cell (its editors and gizmo toggles) or ref. */
export const BuilderItemSection: React.FC = () => {
  const active = useCellBuilderStore((s) => s.active);
  const selection = useCellBuilderStore((s) => s.selection);
  const refs = useCellBuilderStore((s) => s.refs);
  if (!active || !selection) return null;
  const ref = selectedRef(selection, refs);
  if (selection.kind === "ref" && !ref) return null;
  return (
    <div className={frame} data-builder-section="item">
      <div className="font-bold mb-1">{ref ? ref.kindLabel || "Item" : "Procedural cell"}</div>
      {ref ? <RefItem ref_={ref} /> : <SelectionSection selection={selection} />}
    </div>
  );
};

/** Order 72: the space the pick is in -- not for a space itself, which the item section shows. */
export const BuilderSpaceSection: React.FC = () => {
  const s = useCellBuilderStore();
  const { space: name } = useBuilderPlace();
  const space = name ? Object.values(s.cells).find((c) => c.kind === "cell" && c.name === name) : undefined;
  if (!s.active || !space || s.selection?.cellId === space.id) return null;
  const included = isItemIncluded(space);
  return (
    <div className={frame} data-builder-section="space">
      <div className="flex items-center gap-1 mb-1">
        <span className="font-bold truncate">Space {space.name}</span>
        <button
          className="ml-auto text-blue-300 hover:underline"
          title="Select this space"
          onClick={() => s.setSelection({ kind: "cell", cellId: space.id })}
        >
          Select
        </button>
      </div>
      <div className="flex flex-col gap-1 px-1">
        <NameField value={space.name} label={`Name of space ${space.name}`} onCommit={(v) => s.renameCell(space.id, v)} />
        <div className="text-gray-400">{boxLine(space)}</div>
        <label className="flex items-center gap-1" title="Whether the build takes this space (its INCLUDE)">
          <input
            type="checkbox"
            aria-label={`Include space ${space.name} in build`}
            checked={included}
            onChange={(e) => s.setCellsIncluded([space.id], e.target.checked)}
          />
          Include in build
        </label>
      </div>
    </div>
  );
};

/** Order 74: the structure the pick is in, or the active one with nothing picked. */
export const BuilderStructureSection: React.FC = () => {
  const s = useCellBuilderStore();
  const { structure: name } = useBuilderPlace();
  const group = name ? s.groups.find((g) => g.name === name) : undefined;
  if (!s.active || !group) return null;
  const included = isGroupIncluded(group);
  return (
    <div className={frame} data-builder-section="structure">
      <div className="flex items-center gap-1 mb-1">
        <GroupColourInput name={group.name} />
        <span className="font-bold truncate">Structure {group.name}</span>
      </div>
      <div className="flex flex-col gap-1 px-1">
        <NameField
          value={group.name}
          label={`Name of structure ${group.name}`}
          title="Structure name (also the compiled structure's name)"
          onCommit={(v) => renameStructure(group.name, v)}
        />
        <label className="flex items-center gap-1">
          <span className="text-gray-300">blueprint</span>
          <GroupBlueprintSelect group={group} />
        </label>
        <label
          className="flex items-center gap-1"
          title={included ? "Built -- untick to leave this structure out of the next compile" : "Left out of the build -- tick to build it again"}
        >
          <input
            type="checkbox"
            role="switch"
            aria-label={`Include structure ${group.name} in build`}
            aria-checked={included}
            checked={included}
            onChange={(e) => s.setGroupIncluded(group.name, e.target.checked)}
          />
          Include in build
        </label>
      </div>
    </div>
  );
};
