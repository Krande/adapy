// The parameter editors of the cell builder's selection detail: the shared control classes, and
// one parameter as a compact `Field` (PropertyItemLayout) whose help is the engine's for that
// column. Each writes `BuilderCell.params[column]`; anything not edited here still round-trips
// untouched through the params.

import React from "react";
import { useCellBuilderStore, type BuilderCell } from "@/state/cellBuilderStore";
import { Field, FieldCheck, propertyInputCls } from "./PropertyItemLayout";

export const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
export const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

/** The doc list a cell's row is kept in, which its field help is keyed by. */
export function docListOf(cell: Pick<BuilderCell, "kind">): string {
  return cell.kind === "equipment" ? "equipments" : cell.kind === "opening" ? "openings" : cell.kind === "loft" ? "loft_members" : "spaces";
}

const help = (cell: BuilderCell, column: string) => ({ list: docListOf(cell), column });

/** A boolean column as a small checkbox, for an inline field. */
export const ParamCheck: React.FC<{ cell: BuilderCell; column: string; label: string }> = ({ cell, column, label }) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  return (
    <FieldCheck
      label={label}
      ariaLabel={`${label} (${column})`}
      help={help(cell, column)}
      checked={Boolean(cell.params[column])}
      onChange={(v) => setCellParam(cell.id, column, v || null)}
    />
  );
};

/** A number column as a field. */
export const ParamNumber: React.FC<{ cell: BuilderCell; column: string; label: string; step?: number }> = ({ cell, column, label, step = 0.1 }) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const value = cell.params[column];
  return (
    <Field label={label} help={help(cell, column)}>
      <input
        type="number"
        step={step}
        className={`${propertyInputCls} w-24`}
        aria-label={`${label} (${column})`}
        value={typeof value === "number" ? value : ""}
        onChange={(e) => setCellParam(cell.id, column, e.target.value === "" ? null : Number(e.target.value))}
      />
    </Field>
  );
};

/** A text column as a field. */
export const ParamText: React.FC<{ cell: BuilderCell; column: string; label: string }> = ({ cell, column, label }) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const value = cell.params[column];
  return (
    <Field label={label} help={help(cell, column)}>
      <input
        type="text"
        className={`${propertyInputCls} w-full`}
        aria-label={`${label} (${column})`}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => setCellParam(cell.id, column, e.target.value || null)}
      />
    </Field>
  );
};

/** A column of a few values as a select (an empty choice leaves it unset). */
export const ParamSelect: React.FC<{ cell: BuilderCell; column: string; label: string; options: readonly string[] }> = ({ cell, column, label, options }) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const value = cell.params[column];
  return (
    <Field label={label} help={help(cell, column)}>
      <select
        className={`${propertyInputCls} w-full`}
        aria-label={`${label} (${column})`}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => setCellParam(cell.id, column, e.target.value || null)}
      >
        <option value=""></option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </Field>
  );
};

/** A row of number columns sharing one field (COG x / y / z). */
export const ParamNumbersInline: React.FC<{ cell: BuilderCell; label: string; columns: readonly { column: string; label: string }[]; step?: number }> = ({
  cell,
  label,
  columns,
  step = 0.1,
}) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  return (
    <Field label={label} help={help(cell, columns[0].column)} inline>
      {columns.map(({ column, label: l }) => {
        const value = cell.params[column];
        return (
          <label key={column} className="inline-flex min-w-0 flex-1 items-center gap-0.5">
            <span className="text-gray-500">{l}</span>
            <input
              type="number"
              step={step}
              className={`${propertyInputCls} w-full`}
              aria-label={`${label} ${l} (${column})`}
              value={typeof value === "number" ? value : ""}
              onChange={(e) => setCellParam(cell.id, column, e.target.value === "" ? null : Number(e.target.value))}
            />
          </label>
        );
      })}
    </Field>
  );
};
