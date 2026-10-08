// The Sources tab's "Sets" panel: the collection's named sets of branches (`@/assets/treeSets`),
// which one narrows the tree, and the active one's set-wide controls.
//
// Membership is edited IN THE TREE, not here: "Edit" shows the whole tree with a membership box on
// every row and members tinted (`AssetTree`'s `editing`), so the panel never repeats the tree as a
// second list. Members can also be added from a row's right-click menu.
//
// The sets are shared by everyone in the scope; which one is ON is this viewer's own.

import React, { useRef, useState } from "react";

import { providerIdTitle } from "@/assets/providerNames";
import { loadsProvider, type ProviderChoice, type TreeSet, type TreeSetMember } from "@/assets/treeSets";
import { useProviderName } from "@/state/providerNamesStore";

const CHIP = "h-6 px-2 rounded-full border text-[11px] disabled:opacity-50";
const CHIP_OFF = "border-gray-600 text-gray-300 hover:bg-gray-700";
const CHIP_ON = "bg-blue-500/25 border-blue-400 text-blue-100";

/** The set's name while it is edited: saved on Enter or leaving the field, Escape puts it back. */
const SetNameField: React.FC<{ name: string; disabled: boolean; onRename: (name: string) => void }> = ({ name, disabled, onRename }) => {
  const [value, setValue] = useState(name);
  // Escape blurs too; the blur must not then save what Escape just threw away.
  const cancelled = useRef(false);
  const save = () => {
    const next = value.trim();
    if (!cancelled.current && next && next !== name) onRename(next);
    else setValue(name);
    cancelled.current = false;
  };
  return (
    <input
      aria-label="Set name"
      title="Rename the set (Enter to save, Esc to undo)"
      disabled={disabled}
      className="h-6 px-2 rounded-md bg-gray-800 border border-gray-600 focus:border-blue-400 text-[11px] text-gray-100 w-36 outline-none"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        else if (e.key === "Escape") {
          cancelled.current = true;
          (e.target as HTMLInputElement).blur();
        }
      }}
      onBlur={save}
    />
  );
};

const TreeSetsPanel: React.FC<{
  sets: readonly TreeSet[];
  activeId: string | null;
  /** The active set's members the tree does not hold. */
  missing: readonly TreeSetMember[];
  /** The selection's topmost rows, as members -- what a new set starts from. */
  selected: readonly TreeSetMember[];
  busy: boolean;
  error: string | null;
  onActivate: (id: string | null) => void;
  onCreate: (name: string, members: readonly TreeSetMember[]) => void;
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
  /** The providers whose geometry is in this collection -- what a member can choose between. */
  providers: readonly string[];
  /** Whose geometry members load (`null` is every provider), in one write. */
  onSetProviders: (id: string, choices: readonly ProviderChoice[]) => void;
  /** The active set's "Load set" control, built by the tab (it owns the loads). */
  loadControl: React.ReactNode;
  /** Editing the active set's members in the tree: the whole tree shows, with a box per row. */
  editing: boolean;
  onEditing: (on: boolean) => void;
}> = ({ sets, activeId, missing, selected, busy, error, onActivate, onCreate, onRename, onDelete, providers, onSetProviders, loadControl, editing, onEditing }) => {
  const providerName = useProviderName();
  const [naming, setNaming] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const active = sets.find((s) => s.id === activeId) ?? null;

  const create = () => {
    if (!naming?.trim()) return;
    onCreate(naming, selected);
    setNaming(null);
  };

  return (
    <div className="px-2 py-1.5 space-y-1.5 border-b border-gray-700/70 text-xs">
      <div className="flex flex-wrap items-center gap-1">
        <button type="button" className={`${CHIP} ${activeId === null ? CHIP_ON : CHIP_OFF}`} onClick={() => onActivate(null)}>
          All
        </button>
        {sets.map((s) =>
          renaming?.id === s.id ? (
            <input
              key={s.id}
              autoFocus
              aria-label="Set name"
              className="h-6 px-2 rounded-full bg-gray-800 border border-blue-400 text-[11px] text-gray-100 w-28 outline-none"
              value={renaming.name}
              onChange={(e) => setRenaming({ id: s.id, name: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  onRename(s.id, renaming.name);
                  setRenaming(null);
                } else if (e.key === "Escape") setRenaming(null);
              }}
              onBlur={() => setRenaming(null)}
            />
          ) : (
            <button
              key={s.id}
              type="button"
              title={`${s.members.length} branch${s.members.length === 1 ? "" : "es"}${s.created_by ? ` · by ${s.created_by}` : ""} — double-click to rename`}
              className={`${CHIP} ${s.id === activeId ? CHIP_ON : CHIP_OFF}`}
              onClick={() => onActivate(s.id)}
              onDoubleClick={() => setRenaming({ id: s.id, name: s.name })}
            >
              {s.name}
              <span className={`ml-1 ${s.id === activeId ? "text-blue-200/70" : "text-gray-500"}`}>{s.members.length}</span>
            </button>
          ),
        )}
        {naming === null ? (
          <button
            type="button"
            className={`${CHIP} ${CHIP_OFF} w-6 px-0`}
            disabled={busy}
            title={selected.length ? `New set from the ${selected.length} selected row(s)` : "New set (empty -- tick rows in the tree to fill it)"}
            onClick={() => setNaming("")}
          >
            +
          </button>
        ) : (
          <input
            autoFocus
            aria-label="New set name"
            placeholder={selected.length ? `Name (${selected.length} selected)` : "Name"}
            className="h-6 px-2 rounded-full bg-gray-800 border border-blue-400 text-[11px] text-gray-100 placeholder:text-gray-500 w-32 outline-none"
            value={naming}
            onChange={(e) => setNaming(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") create();
              else if (e.key === "Escape") setNaming(null);
            }}
            onBlur={() => (naming.trim() ? create() : setNaming(null))}
          />
        )}
      </div>

      {active && (
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            className={`${CHIP} ${editing ? CHIP_ON : CHIP_OFF}`}
            aria-pressed={editing}
            title={
              editing
                ? "Done: narrow the tree to the set again"
                : `Edit "${active.name}": show the whole tree, and tick or untick rows to add or take them out`
            }
            onClick={() => onEditing(!editing)}
          >
            {editing ? "Done" : "Edit"}
          </button>
          {editing && <SetNameField key={active.id} name={active.name} disabled={busy} onRename={(n) => onRename(active.id, n)} />}
          {!editing && loadControl}
          {providers.length > 1 && active.members.length > 0 && (
            <span className="flex items-center gap-1" title="Whose geometry every member loads; a member's own choice shows on its row in the tree">
              {providers.map((p) => {
                const on = active.members.every((m) => loadsProvider(m, p));
                return (
                  <button
                    key={p}
                    type="button"
                    disabled={busy}
                    title={`${providerName(p)}: ${on ? "loaded for every member -- click to load it for none" : "not loaded for every member -- click to load it for all"}\n${providerIdTitle(p)}`}
                    className={`px-1.5 rounded-sm border text-[10px] disabled:opacity-50 ${
                      on ? "bg-emerald-800/80 border-emerald-600 text-white" : "border-gray-600 text-gray-500 line-through"
                    }`}
                    onClick={() =>
                      // On for every member, or off for every member; each keeps its other choices.
                      onSetProviders(
                        active.id,
                        active.members.map((m) => {
                          const own = new Set(m.providers ?? providers);
                          if (on) own.delete(p);
                          else own.add(p);
                          return { memberId: m.id, providers: providers.every((q) => own.has(q)) ? null : [...own] };
                        }),
                      )
                    }
                  >
                    {providerName(p)}
                  </button>
                );
              })}
            </span>
          )}
          {missing.length > 0 && (
            <span className="text-amber-300 cursor-help" title={`Not in this tree (not published in what is shown, or its branch is not fetched yet):\n${missing.map((m) => m.label).join("\n")}`}>
              {missing.length} not in tree
            </span>
          )}
          <button
            type="button"
            className="ml-auto text-gray-500 hover:text-red-300 disabled:opacity-50"
            disabled={busy}
            title={`Delete "${active.name}" for everyone in this scope`}
            onClick={() => {
              if (window.confirm(`Delete the set "${active.name}" for everyone in this scope?`)) onDelete(active.id);
            }}
          >
            Delete
          </button>
        </div>
      )}
      {active && active.members.length === 0 && !editing && (
        <div className="text-gray-500">Empty — press Edit and tick rows in the tree to add them.</div>
      )}
      {error && <div className="text-red-300 break-words">{error}</div>}
    </div>
  );
};

export default TreeSetsPanel;
