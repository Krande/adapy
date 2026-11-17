// A selected item's free-form metadata: add, rename, edit and remove keys.

import React from "react";
import { addMetadataKey, formatMetadataValue, metaOrNull, removeMetadataKey, renameMetadataKey, setMetadataValue, type MetaMap } from "@/utils/cellbuilder/metadata";
import { btn, inputCls } from "./fields";

// User-defined extended metadata editor — free-form key/value rows the compiler
// ignores but the DB round-trips, so a viewer/integration can attach its own
// config to any topology instance. Generic over the backing store: a cell's
// params.METADATA or a loft member's METADATA (``onCommit`` persists the next
// map, or null to clear). All map math lives in the pure `metadata` helpers;
// this component only wires the rows to them.
export const MetadataFields: React.FC<{
  meta: MetaMap;
  onCommit: (next: MetaMap | null) => void;
  idPrefix: string;
}> = ({ meta, onCommit, idPrefix }) => {
  const [open, setOpen] = React.useState(false);
  const entries = Object.entries(meta);
  // Commit a next map, but skip a no-op (helpers return the SAME ref when
  // nothing changed) so an identity edit doesn't push an undo step / mark dirty.
  // Empty -> null so the caller drops the key entirely (no empty METADATA={}).
  const push = (next: MetaMap) => {
    if (next !== meta) onCommit(metaOrNull(next));
  };
  const renameKey = (oldKey: string, newKey: string) =>
    push(renameMetadataKey(meta, oldKey, newKey));
  const setValue = (key: string, value: string) =>
    push(setMetadataValue(meta, key, value));
  const remove = (key: string) => push(removeMetadataKey(meta, key));
  const add = () => push(addMetadataKey(meta));
  return (
    <div className="border-t border-gray-600/60 pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>▸</span>
        <span className="font-semibold">Metadata</span>
        <span className="text-gray-400">({entries.length})</span>
      </button>
      {open && (
        <div className="flex flex-col gap-1 px-1 pt-1">
          {entries.length === 0 && (
            <div className="text-gray-500 italic">
              No metadata. Add your own fields — kept in the DB, ignored by the compiler.
            </div>
          )}
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-center gap-1">
              <input
                className={`${inputCls} w-24`}
                defaultValue={k}
                key={idPrefix + "|" + k}
                onBlur={(e) => renameKey(k, e.target.value.trim())}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                }}
                title="Field name"
              />
              <input
                className={`${inputCls} flex-1 min-w-0`}
                // Uncontrolled + parse-on-blur: numbers/bools/JSON are only
                // coerced once the field is committed, so typing "5." or "-"
                // isn't clobbered mid-keystroke. `key` includes the formatted
                // value so an external change (undo/redo) reseeds the field.
                defaultValue={formatMetadataValue(v)}
                key={idPrefix + "|v|" + k + "|" + formatMetadataValue(v)}
                onBlur={(e) => setValue(k, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                }}
                title="Value — numbers, true/false and JSON are stored typed; anything else stays text"
              />
              <button
                className="px-1 rounded-sm text-gray-400 hover:bg-gray-600 hover:text-white"
                onClick={() => remove(k)}
                title="Remove field"
              >
                ✕
              </button>
            </div>
          ))}
          <button className={btn} onClick={add}>
            + Add field
          </button>
        </div>
      )}
    </div>
  );
};
