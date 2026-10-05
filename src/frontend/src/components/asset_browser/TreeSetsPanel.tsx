// The Sources tab's "Sets" panel: the collection's named sets of branches (`@/assets/treeSets`),
// which one narrows the tree, and their members.
//
// The sets are shared by everyone in the scope; which one is ON is this viewer's own. Members are
// added from the tree selection -- here, or from a row's right-click menu.

import React, { useState } from "react";

import { loadsProvider, type ProviderChoice, type TreeSet, type TreeSetMember } from "@/assets/treeSets";

const BTN = "px-1.5 py-0.5 rounded-sm border border-gray-600 text-gray-200 hover:bg-gray-700 disabled:opacity-50";

const TreeSetsPanel: React.FC<{
  sets: readonly TreeSet[];
  activeId: string | null;
  /** The active set's members the tree does not hold. */
  missing: readonly TreeSetMember[];
  /** The selection's topmost rows, as members -- what "add" adds. */
  selected: readonly TreeSetMember[];
  busy: boolean;
  error: string | null;
  onActivate: (id: string | null) => void;
  onCreate: (name: string, members: readonly TreeSetMember[]) => void;
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
  onAdd: (id: string, members: readonly TreeSetMember[]) => void;
  onRemove: (id: string, memberIds: readonly string[]) => void;
  /** Select a member's row in the tree. */
  onReveal: (id: string) => void;
  /** The providers whose geometry is in this collection -- what a member can choose between. */
  providers: readonly string[];
  /** Whose geometry members load (`null` is every provider), in one write. */
  onSetProviders: (id: string, choices: readonly ProviderChoice[]) => void;
  /** The active set's "Load set" control, built by the tab (it owns the loads). */
  loadControl: React.ReactNode;
}> = ({
  sets,
  activeId,
  missing,
  selected,
  busy,
  error,
  onActivate,
  onCreate,
  onRename,
  onDelete,
  onAdd,
  onRemove,
  onReveal,
  providers,
  onSetProviders,
  loadControl,
}) => {
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const active = sets.find((s) => s.id === activeId) ?? null;
  const missingIds = new Set(missing.map((m) => m.id));
  const memberIds = new Set(active?.members.map((m) => m.id) ?? []);
  const toAdd = selected.filter((m) => !memberIds.has(m.id));
  const toRemove = selected.filter((m) => memberIds.has(m.id));

  // A member's choice with `provider` toggled, written back as `null` when it is every provider
  // again -- so a provider published later is loaded too, rather than frozen out.
  const toggled = (current: readonly string[] | undefined, provider: string): string[] | null => {
    const on = new Set(current ?? providers);
    if (!on.delete(provider)) on.add(provider);
    return providers.every((p) => on.has(p)) ? null : [...on].sort();
  };
  // A render function, called rather than mounted: declared in here, a component would be a new
  // type every render.
  const providerChips = (isOn: (p: string) => boolean, onToggle: (p: string) => void, title: (p: string) => string) => (
    <>
      {providers.map((p) => (
        <button
          key={p}
          type="button"
          disabled={busy}
          title={title(p)}
          onClick={() => onToggle(p)}
          className={`px-1 rounded-sm text-[10px] border disabled:opacity-50 ${
            isOn(p) ? "bg-emerald-800 border-emerald-600 text-white" : "border-gray-600 text-gray-500 line-through"
          }`}
        >
          {p}
        </button>
      ))}
    </>
  );

  const create = () => {
    if (!name.trim()) return;
    onCreate(name, selected);
    setName("");
  };

  return (
    <div className="px-1 py-1 space-y-1.5 border-b border-gray-700 text-xs">
      <div className="flex flex-wrap gap-1">
        <button
          type="button"
          className={`${BTN} ${activeId === null ? "bg-blue-700 border-blue-500 text-white" : ""}`}
          onClick={() => onActivate(null)}
        >
          All
        </button>
        {sets.map((s) =>
          renaming?.id === s.id ? (
            <input
              key={s.id}
              autoFocus
              aria-label="Set name"
              className="px-1 py-0.5 rounded-sm bg-gray-800 border border-blue-500 text-gray-100 w-28"
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
              className={`${BTN} ${s.id === activeId ? "bg-blue-700 border-blue-500 text-white" : ""}`}
              onClick={() => onActivate(s.id)}
              onDoubleClick={() => setRenaming({ id: s.id, name: s.name })}
            >
              {s.name}
              <span className="ml-1 text-gray-400">{s.members.length}</span>
            </button>
          ),
        )}
      </div>

      <div className="flex items-center gap-1">
        <input
          aria-label="New set name"
          placeholder={selected.length ? `New set from ${selected.length} selected` : "New set (empty)"}
          className="flex-1 min-w-0 px-1.5 py-0.5 rounded-sm bg-gray-800 border border-gray-600 text-gray-100 placeholder:text-gray-500"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <button type="button" className={BTN} disabled={busy || !name.trim()} onClick={create}>
          Create
        </button>
      </div>

      {active && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-gray-400 mr-auto truncate">{active.name}</span>
            <button
              type="button"
              className={BTN}
              disabled={busy || toAdd.length === 0}
              title="Add the selected rows (and everything under them)"
              onClick={() => onAdd(active.id, toAdd)}
            >
              Add selected{toAdd.length ? ` (${toAdd.length})` : ""}
            </button>
            <button
              type="button"
              className={BTN}
              disabled={busy || toRemove.length === 0}
              onClick={() => onRemove(active.id, toRemove.map((m) => m.id))}
            >
              Remove selected{toRemove.length ? ` (${toRemove.length})` : ""}
            </button>
            <button
              type="button"
              className={`${BTN} text-red-300`}
              disabled={busy}
              title="Delete this set for everyone in the scope"
              onClick={() => {
                if (window.confirm(`Delete the set "${active.name}" for everyone in this scope?`)) onDelete(active.id);
              }}
            >
              Delete
            </button>
          </div>
          {loadControl}
          {providers.length > 1 && active.members.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-gray-400">Geometry for every member:</span>
              {providerChips(
                (p) => active.members.every((m) => loadsProvider(m, p)),
                (p) => {
                  // On for every member, or off for every member; each keeps its other choices.
                  const everyOn = active.members.every((m) => loadsProvider(m, p));
                  onSetProviders(
                    active.id,
                    active.members.map((m) => {
                      const own = new Set(m.providers ?? providers);
                      if (everyOn) own.delete(p);
                      else own.add(p);
                      return { memberId: m.id, providers: providers.every((q) => own.has(q)) ? null : [...own] };
                    }),
                  );
                },
                (p) => `Load ${p}'s geometry for every member, or for none`,
              )}
            </div>
          )}
          <ul className="max-h-40 overflow-auto space-y-0.5">
            {active.members.length === 0 && (
              <li className="text-gray-500">Empty — select rows in the tree and add them. An empty set draws nothing.</li>
            )}
            {active.members.map((m) => (
              <li key={m.id} className="flex items-center gap-1">
                <button
                  type="button"
                  className={`flex-1 min-w-0 truncate text-left hover:underline ${missingIds.has(m.id) ? "text-amber-300" : "text-gray-200"}`}
                  title={missingIds.has(m.id) ? `${m.id} — not in this tree: not published in what is shown, or its branch is not fetched yet` : m.id}
                  disabled={missingIds.has(m.id)}
                  onClick={() => onReveal(m.id)}
                >
                  {m.label}
                  {missingIds.has(m.id) && <span className="ml-1 text-amber-400/80">(not in tree)</span>}
                </button>
                {providers.length > 1 && (
                  providerChips(
                    (p) => loadsProvider(m, p),
                    (p) => onSetProviders(active.id, [{ memberId: m.id, providers: toggled(m.providers, p) }]),
                    (p) => `${loadsProvider(m, p) ? "Loads" : "Does not load"} ${p}'s geometry for ${m.label} — click to switch`,
                  )
                )}
                <button
                  type="button"
                  aria-label={`Remove ${m.label}`}
                  className="px-1 text-gray-400 hover:text-red-300 disabled:opacity-50"
                  disabled={busy}
                  onClick={() => onRemove(active.id, [m.id])}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="text-gray-500">Sets are shared with everyone in this scope; the one you pick is just for you.</div>
      {error && <div className="text-red-300 break-words">{error}</div>}
    </div>
  );
};

export default TreeSetsPanel;
