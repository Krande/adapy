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

/** The value a checkbox's onChange writes through `setCellParam`: checked is always
 *  `true`. Unchecked is the interesting case -- `setCellParam(..., null)` DELETES the key rather
 *  than writing `false`, so unchecking a `defaultOn` (model default `True`, e.g. the Spaces
 *  sheet's GRID_*_CREATE) column with the plain rule left no key behind, and the model default
 *  then brought the box back to checked on the next read (reload, a fresh compile). `defaultOn`
 *  writes an explicit `false` instead, which sticks. A default-false column (the common case)
 *  keeps the old rule: unchecking removes the key, since "absent" already reads as off. */
export function checkboxParamValue(checked: boolean, defaultOn?: boolean): boolean | null {
  return checked ? true : defaultOn ? false : null;
}

/** A boolean column as a small checkbox, for an inline field. See `checkboxParamValue` for what
 *  `defaultOn` changes on write; `defaultOn` changes the READ the same way: a `defaultOn`
 *  column (e.g. GRID_*_CREATE, model default `True`) with no key at all -- never touched, or
 *  unchecked before the `checkboxParamValue` fix existed -- is on by that default, so the box must
 *  show checked, not unchecked, for an absent key. A default-false column keeps the plain read:
 *  only an explicit truthy value checks it. */
export const ParamCheck: React.FC<{ cell: BuilderCell; column: string; label: string; defaultOn?: boolean }> = ({ cell, column, label, defaultOn }) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  return (
    <FieldCheck
      label={label}
      ariaLabel={`${label} (${column})`}
      help={help(cell, column)}
      checked={defaultOn ? cell.params[column] !== false : Boolean(cell.params[column])}
      onChange={(v) => setCellParam(cell.id, column, checkboxParamValue(v, defaultOn))}
    />
  );
};

/** A number column as a field. */
export const ParamNumber: React.FC<{ cell: BuilderCell; column: string; label: string; step?: number }> = ({ cell, column, label, step = 0.1 }) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const value = cell.params[column];
  return (
    <Field label={label} help={help(cell, column)} useEngineLabel>
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
    <Field label={label} help={help(cell, column)} useEngineLabel>
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

/** Whether a datalist `<input>`'s "input" event is an actual PICK of one of `suggestions`, not a
 *  keystroke that happens to land on a suggestion's exact text on the way to a longer value
 *  (typing "A10" passes through the draft "A1", which must not commit early just because "A1" is
 *  itself a suggestion). A keystroke's native event always carries an `inputType`
 *  ("insertText", "deleteContentBackward", ...); picking an option from the list does not --
 *  Chromium fires a plain `Event` with no `inputType` at all. Firefox instead fires an
 *  `InputEvent` whose `inputType` is `"insertReplacementText"` for a datalist pick specifically
 *  (never for an ordinary keystroke), so that counts as a pick there too. */
export function isDatalistPick(value: string, suggestions: readonly string[], inputType: string | null | undefined): boolean {
  return (!inputType || inputType === "insertReplacementText") && suggestions.includes(value);
}

/** A text column with datalist suggestions, committed on blur, Enter or picking a suggestion --
 *  not per keystroke, so a suggestion click doesn't fight the caret (ConnectionBuilder's own
 *  free-text-with-suggestions pattern, SectionField). Free text that is not in `suggestions`
 *  still writes: the list only ever offers, never restricts. */
export const ParamCombo: React.FC<{ cell: BuilderCell; column: string; label: string; suggestions: readonly string[] }> = ({ cell, column, label, suggestions }) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const value = cell.params[column];
  const stored = typeof value === "string" ? value : "";
  const [draft, setDraft] = React.useState(stored);
  React.useEffect(() => setDraft(stored), [stored]);
  // Unique per instance: two fields for the same column (e.g. the same cell shown twice,
  // or two cells of the same kind in different panels) must not share one <datalist> id.
  const listId = `param-combo-${React.useId()}`;
  const commit = (v: string) => {
    const t = v.trim();
    if (t !== stored) setCellParam(cell.id, column, t || null);
  };
  return (
    <Field label={label} help={help(cell, column)} useEngineLabel>
      <input
        type="text"
        list={listId}
        className={`${propertyInputCls} w-full`}
        aria-label={`${label} (${column})`}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          const inputType = (e.nativeEvent as InputEvent).inputType;
          if (isDatalistPick(e.target.value, suggestions, inputType)) commit(e.target.value);
        }}
        onBlur={() => commit(draft)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(draft);
          if (e.key === "Escape") setDraft(stored);
        }}
      />
      <datalist id={listId}>
        {suggestions.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
    </Field>
  );
};

/** A column of a few values as a select (an empty choice leaves it unset). */
export const ParamSelect: React.FC<{ cell: BuilderCell; column: string; label: string; options: readonly string[] }> = ({ cell, column, label, options }) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const value = cell.params[column];
  return (
    <Field label={label} help={help(cell, column)} useEngineLabel>
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

// ParamNumbersInline (a row of number columns sharing one field, e.g. COG x/y/z) was removed:
// its one remaining caller, equipment's COG, now shares the Geometry grid's X/Y/Z layout
// as an extra row (PropertyItemLayout's GeometryGrid `extraRows`) instead of its own inline style.
