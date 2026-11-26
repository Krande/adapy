// One layout for every topology item in the Properties panel -- a space, an equipment, an
// opening, a loft bay, a structure, a plugin's ref -- so they read the same: where it is first
// (the parents line: "in <space> · <structure> [blueprint]", each a link that selects it), then
// what it is (a type badge and its name, renamed in place), then its geometry (position and size
// in one compact X / Y / Z grid), then its groups of fields. Exported on `@/viewer-core/layout`,
// so a plugin's form for its own ref is drawn the same way (`BuilderRefEdit.form`).
//
// `Field` is the row a group is made of: a short label carrying its help as a tooltip, and the
// value; `inline` puts several small inputs on the one row (Grid X / Y / Z, SE0..SE5). Its help
// is text, or a doc list and column read from the engine catalog's field help (`fieldHelp`).

import React from "react";
import { useFieldHelp, useFieldLabel } from "@/state/cellbuilder/fieldHelp";

type Vec3 = readonly [number, number, number];

export const propertyInputCls = "min-w-0 text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

/** Where an item sits, each part a link that selects it. */
export interface PropertyItemParents {
  space?: { name: string; onSelect?: () => void };
  structure?: { name: string; blueprint?: string; onSelect?: () => void };
}

/** An extra row sharing the geometry grid's X/Y/Z layout, writing through its own `onChange`
 *  rather than `PropertyItemGeometry.onChange` -- e.g. an equipment's COG, which goes
 *  through `setCellParam`, not `updateCell`/`setCellRotation`. Always editable. */
export interface GeometryExtraRow {
  /** Unique among a geometry's extra rows. */
  key: string;
  label: string;
  value: Vec3;
  /** Input step; default 0.1. */
  step?: number;
  onChange: (next: Vec3) => void;
}

/** An item's box. Editable where `onChange` is given, row by row as `editable` allows (default: all). */
export interface PropertyItemGeometry {
  origin: Vec3;
  size: Vec3;
  rotation?: Vec3;
  onChange?: (next: { origin: Vec3; size: Vec3; rotation?: Vec3 }) => void;
  editable?: { origin?: boolean; size?: boolean; rotation?: boolean };
  /** Extra rows in the same grid, below Position / Size / Rotation° (e.g. an equipment's COG). */
  extraRows?: readonly GeometryExtraRow[];
}

export interface PropertyItemGroup {
  title: string;
  fields: React.ReactNode;
}

export interface PropertyItemLayoutProps {
  /** What kind of item it is ("Space", "Pump", a ref's kind label). */
  badge: string;
  name: string;
  /** Given: the name is an input, committed on blur or Enter (an unchanged or empty name is not). */
  onRename?: (name: string) => void;
  parents?: PropertyItemParents;
  geometry?: PropertyItemGeometry;
  groups?: readonly PropertyItemGroup[];
  /** Drawn at the end of the header row (an item's own small actions). */
  actions?: React.ReactNode;
  /** On the outer element, for a host to find it. */
  dataAttrs?: Readonly<Record<string, string>>;
}

const LINK = "text-blue-300 hover:underline disabled:no-underline disabled:text-gray-300";

/** "in <space> · <structure> [blueprint]": where the item is, each part selecting it. */
const ParentsLine: React.FC<{ parents: PropertyItemParents }> = ({ parents: { space, structure } }) => {
  if (!space && !structure) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-1 text-[11px] text-gray-400" data-property-parents="">
      <span>in</span>
      {space && (
        <button type="button" className={LINK} disabled={!space.onSelect} onClick={space.onSelect} title={`Select space ${space.name}`}>
          {space.name}
        </button>
      )}
      {space && structure && <span aria-hidden="true">·</span>}
      {structure && (
        <button
          type="button"
          className={LINK}
          disabled={!structure.onSelect}
          onClick={structure.onSelect}
          title={`Select structure ${structure.name}`}
        >
          {structure.name}
        </button>
      )}
      {structure?.blueprint && <span className="text-gray-500">[{structure.blueprint}]</span>}
    </div>
  );
};

/** The name: plain text, or an input that commits on blur or Enter. */
const NameInPlace: React.FC<{ name: string; onRename?: (name: string) => void; badge: string }> = ({ name, onRename, badge }) => {
  if (!onRename) return <span className="min-w-0 truncate font-semibold">{name}</span>;
  return (
    <input
      type="text"
      key={name}
      defaultValue={name}
      aria-label={`Name of ${badge.toLowerCase()} ${name}`}
      title="Rename (Enter or leave the field to apply)"
      className="min-w-0 flex-1 rounded-sm border border-transparent bg-transparent px-1 font-semibold text-gray-100 hover:border-gray-600 focus:border-gray-500 focus:bg-gray-700"
      onBlur={(e) => {
        const next = e.target.value.trim();
        if (next && next !== name) onRename(next);
        else e.target.value = name;
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          e.currentTarget.value = name;
          e.currentTarget.blur();
        }
      }}
    />
  );
};

const fmt = (v: number) => Number(v.toFixed(3));

/** Position and size (and rotation, and an item's extra rows) in one compact grid: a
 *  row each, a column per axis, one input style throughout. */
const GeometryGrid: React.FC<{ geometry: PropertyItemGeometry }> = ({ geometry: g }) => {
  type Row = { key: string; label: string; value: Vec3; min?: number; step: number; editable: boolean; write: (axis: number, v: number) => void };
  const write = (key: "origin" | "size" | "rotation", axis: number, v: number) => {
    const next = { origin: g.origin, size: g.size, rotation: g.rotation };
    const vec = [...(next[key] ?? [0, 0, 0])] as [number, number, number];
    vec[axis] = v;
    next[key] = vec;
    g.onChange?.(next);
  };
  const builtIn: Row[] = [
    { key: "origin", label: "Position", value: g.origin, step: 0.1, editable: !!g.onChange && (g.editable?.origin ?? true), write: (axis, v) => write("origin", axis, v) },
    { key: "size", label: "Size", value: g.size, min: 0, step: 0.1, editable: !!g.onChange && (g.editable?.size ?? true), write: (axis, v) => write("size", axis, v) },
    ...(g.rotation
      ? [{ key: "rotation", label: "Rotation°", value: g.rotation, step: 15, editable: !!g.onChange && (g.editable?.rotation ?? true), write: (axis, v) => write("rotation", axis, v) } as Row]
      : []),
  ];
  const extra: Row[] = (g.extraRows ?? []).map((r) => ({
    key: r.key,
    label: r.label,
    value: r.value,
    step: r.step ?? 0.1,
    editable: true,
    write: (axis, v) => {
      const vec = [...r.value] as [number, number, number];
      vec[axis] = v;
      r.onChange(vec);
    },
  }));
  const rows = [...builtIn, ...extra];
  return (
    <div className="grid grid-cols-[auto_1fr_1fr_1fr] items-center gap-x-1 gap-y-0.5" data-property-geometry="">
      <span />
      {["X", "Y", "Z"].map((a) => (
        <span key={a} className="text-center text-[10px] text-gray-500">
          {a}
        </span>
      ))}
      {rows.map((r) => (
        <React.Fragment key={r.key}>
          <span className="pr-1 text-gray-300">{r.label}</span>
          {r.value.map((v, i) =>
            r.editable ? (
              <input
                key={i}
                type="number"
                step={r.step}
                min={r.min}
                className={`${propertyInputCls} w-full`}
                value={fmt(v)}
                aria-label={`${r.label} ${"XYZ"[i]}`}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  if (e.target.value === "" || !Number.isFinite(n) || (r.min !== undefined && n <= r.min)) return;
                  r.write(i, n);
                }}
              />
            ) : (
              <span key={i} className="truncate text-right text-gray-400" aria-label={`${r.label} ${"XYZ"[i]}`}>
                {fmt(v)}
              </span>
            ),
          )}
        </React.Fragment>
      ))}
    </div>
  );
};

/** A titled group of fields, as the item layout draws its groups: for a section that is one group
 *  of its own beside an item (the FEA sets of a picked element). */
export const PropertyGroup: React.FC<{ group: PropertyItemGroup }> = ({ group }) => <Group group={group} />;

/** A titled group of fields. */
const Group: React.FC<{ group: PropertyItemGroup }> = ({ group }) => (
  <div className="flex flex-col gap-0.5" data-property-group={group.title}>
    <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{group.title}</div>
    {group.fields}
  </div>
);

/** One topology item in the Properties panel: parents, header, geometry, groups (see the file's head). */
export const PropertyItemLayout: React.FC<PropertyItemLayoutProps> = ({ badge, name, onRename, parents, geometry, groups = [], actions, dataAttrs }) => (
  <div className="flex flex-col gap-2 pt-2 text-xs text-white" data-property-item="" {...dataAttrs}>
    {parents && <ParentsLine parents={parents} />}
    <div className="flex min-w-0 items-center gap-1.5" data-property-header="">
      <span className="shrink-0 rounded-sm bg-gray-600/70 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-gray-200">{badge}</span>
      <NameInPlace name={name} onRename={onRename} badge={badge} />
      {actions && <span className="ml-auto flex shrink-0 items-center gap-1">{actions}</span>}
    </div>
    {geometry && <Group group={{ title: "Geometry", fields: <GeometryGrid geometry={geometry} /> }} />}
    {groups.map((g) => (
      <Group key={g.title} group={g} />
    ))}
  </div>
);

/** Where a field's help comes from: the text, or a doc list and column of the engine's field help. */
export type FieldHelpSource = string | { list: string; column: string };

/** The help text of `help` (a lookup in the engine catalog for a list and column). */
export function useHelpText(help: FieldHelpSource | undefined): string | undefined {
  const ref = typeof help === "object" ? help : null;
  const looked = useFieldHelp(ref?.list ?? "", ref?.column ?? "");
  return typeof help === "string" ? help : ref ? looked : undefined;
}

/**
 * The label to show for a field and its tooltip: `help` as a plain string behaves exactly
 * as before -- the caller's own `label`, the string as the tooltip. `{list, column}` also looks up
 * the engine's UI label for that column (`useFieldLabel`), but it replaces the caller's `label`
 * only when the caller opts in with `useEngineLabel`: a single-column editor's own label is
 * almost always the column's name, so showing the engine's label there is correct, but a row that
 * borrows one column's help for several small inputs ("Create" borrowing GRID_X_CREATE, for X / Y
 * / Z; "Exclude" borrowing one of SE0..SE5, for six side checks) is not that column, and swapping
 * in the column's label there replaced "Create" with a full sentence and turned "X" into one too.
 * Without `useEngineLabel` the caller's own `label` is shown, with the engine's label (when there
 * is one) named in the tooltip instead, so the Excel column stays discoverable either way.
 *
 * The tooltip of a `{list, column}` help always names the column first -- "COLUMN — help
 * text", or just "COLUMN" without help text -- whether or not an engine label is shown as the
 * label: a row borrowing another column's help (e.g. "-X"'s SE4) needs its own column named too,
 * not only a field whose label happens to have been swapped for the engine's.
 */
function useFieldLabelAndTooltip(
  help: FieldHelpSource | undefined,
  fallbackLabel: string,
  useEngineLabel = false,
): { label: string; tooltip: string | undefined } {
  const ref = typeof help === "object" ? help : null;
  const helpText = useFieldHelp(ref?.list ?? "", ref?.column ?? "");
  const engineLabel = useFieldLabel(ref?.list ?? "", ref?.column ?? "");
  if (typeof help === "string") return { label: fallbackLabel, tooltip: help };
  if (!ref) return { label: fallbackLabel, tooltip: undefined };
  const label = useEngineLabel && engineLabel ? engineLabel : fallbackLabel;
  const tooltip = helpText ? `${ref.column} — ${helpText}` : ref.column;
  return { label, tooltip };
}

export interface FieldProps {
  label: string;
  /** Shown as the label's tooltip. */
  help?: FieldHelpSource;
  /** Several small inputs share the row (Grid X / Y / Z, SE0..SE5). */
  inline?: boolean;
  /** Show the engine's UI label for `help`'s column instead of `label`, when it has one. Only
   *  for a single-column editor whose own label already names that one column (`ParamNumber` /
   *  `ParamText` / `ParamSelect`); a row that borrows the column's help for several inputs leaves
   *  this unset and keeps its own label. */
  useEngineLabel?: boolean;
  children?: React.ReactNode;
}

/** A compact label / value row of a group. */
export const Field: React.FC<FieldProps> = ({ label, help, inline, useEngineLabel, children }) => {
  const { label: shown, tooltip } = useFieldLabelAndTooltip(help, label, useEngineLabel);
  return (
    // data-field keeps the CALLER's own label (not the engine's override), so a host that finds
    // a field by it (a test, a plugin) is unaffected by whether an engine label is active.
    <div className="grid grid-cols-[minmax(4.5rem,auto)_1fr] items-center gap-x-2" data-field={label}>
      <span className={tooltip ? "truncate cursor-help text-gray-300 underline decoration-dotted decoration-gray-500" : "truncate text-gray-300"} title={tooltip}>
        {shown}
      </span>
      <div className={inline ? "flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5" : "flex min-w-0 items-center gap-1"}>{children}</div>
    </div>
  );
};

/** A small labelled checkbox for an inline field, its help on hover. `useEngineLabel` is the same
 *  opt-in as `Field`'s; every current caller (`ParamCheck`, `SpaceExcludeCheck`) is an inline
 *  check sharing a row's borrowed column help, so none sets it and all keep their own label. */
export const FieldCheck: React.FC<{ label: string; checked: boolean; onChange: (v: boolean) => void; help?: FieldHelpSource; ariaLabel?: string; useEngineLabel?: boolean }> = ({
  label,
  checked,
  onChange,
  help,
  ariaLabel,
  useEngineLabel,
}) => {
  const { label: shown, tooltip } = useFieldLabelAndTooltip(help, label, useEngineLabel);
  return (
    <label className="inline-flex items-center gap-0.5" title={tooltip}>
      <input type="checkbox" checked={checked} aria-label={ariaLabel ?? label} onChange={(e) => onChange(e.target.checked)} />
      {shown}
    </label>
  );
};
