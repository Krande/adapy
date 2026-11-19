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
import { useFieldHelp } from "@/state/cellbuilder/fieldHelp";

type Vec3 = readonly [number, number, number];

export const propertyInputCls = "min-w-0 text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

/** Where an item sits, each part a link that selects it. */
export interface PropertyItemParents {
  space?: { name: string; onSelect?: () => void };
  structure?: { name: string; blueprint?: string; onSelect?: () => void };
}

/** An item's box. Editable where `onChange` is given, row by row as `editable` allows (default: all). */
export interface PropertyItemGeometry {
  origin: Vec3;
  size: Vec3;
  rotation?: Vec3;
  onChange?: (next: { origin: Vec3; size: Vec3; rotation?: Vec3 }) => void;
  editable?: { origin?: boolean; size?: boolean; rotation?: boolean };
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

/** Position and size (and rotation) in one compact grid: a row each, a column per axis. */
const GeometryGrid: React.FC<{ geometry: PropertyItemGeometry }> = ({ geometry: g }) => {
  const rows: { key: "origin" | "size" | "rotation"; label: string; value: Vec3; min?: number; step: number }[] = [
    { key: "origin", label: "Position", value: g.origin, step: 0.1 },
    { key: "size", label: "Size", value: g.size, min: 0, step: 0.1 },
    ...(g.rotation ? [{ key: "rotation" as const, label: "Rotation°", value: g.rotation, step: 15 }] : []),
  ];
  const write = (key: "origin" | "size" | "rotation", axis: number, v: number) => {
    const next = { origin: g.origin, size: g.size, rotation: g.rotation };
    const vec = [...(next[key] ?? [0, 0, 0])] as [number, number, number];
    vec[axis] = v;
    next[key] = vec;
    g.onChange?.(next);
  };
  return (
    <div className="grid grid-cols-[auto_1fr_1fr_1fr] items-center gap-x-1 gap-y-0.5" data-property-geometry="">
      <span />
      {["X", "Y", "Z"].map((a) => (
        <span key={a} className="text-center text-[10px] text-gray-500">
          {a}
        </span>
      ))}
      {rows.map((r) => {
        const editable = !!g.onChange && (g.editable?.[r.key] ?? true);
        return (
          <React.Fragment key={r.key}>
            <span className="pr-1 text-gray-300">{r.label}</span>
            {r.value.map((v, i) =>
              editable ? (
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
                    write(r.key, i, n);
                  }}
                />
              ) : (
                <span key={i} className="truncate text-right text-gray-400" aria-label={`${r.label} ${"XYZ"[i]}`}>
                  {fmt(v)}
                </span>
              ),
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
};

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

export interface FieldProps {
  label: string;
  /** Shown as the label's tooltip. */
  help?: FieldHelpSource;
  /** Several small inputs share the row (Grid X / Y / Z, SE0..SE5). */
  inline?: boolean;
  children?: React.ReactNode;
}

/** A compact label / value row of a group. */
export const Field: React.FC<FieldProps> = ({ label, help, inline, children }) => {
  const text = useHelpText(help);
  return (
    <div className="grid grid-cols-[minmax(4.5rem,auto)_1fr] items-center gap-x-2" data-field={label}>
      <span className={text ? "truncate cursor-help text-gray-300 underline decoration-dotted decoration-gray-500" : "truncate text-gray-300"} title={text}>
        {label}
      </span>
      <div className={inline ? "flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5" : "flex min-w-0 items-center gap-1"}>{children}</div>
    </div>
  );
};

/** A small labelled checkbox for an inline field, its help on hover. */
export const FieldCheck: React.FC<{ label: string; checked: boolean; onChange: (v: boolean) => void; help?: FieldHelpSource; ariaLabel?: string }> = ({
  label,
  checked,
  onChange,
  help,
  ariaLabel,
}) => {
  const text = useHelpText(help);
  return (
    <label className="inline-flex items-center gap-0.5" title={text}>
      <input type="checkbox" checked={checked} aria-label={ariaLabel ?? label} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
};
