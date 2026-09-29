// The Assets tab's "View" panel: how this collection's tree is drawn.
//
// Every control here writes the scope's saved view (`@/services/assetView`),
// shared with everyone in the scope. The provider's suggestion is what applies
// until something is saved, and "Use provider defaults" goes back to it. See
// `@/assets/treeView` for what each setting does.

import React from "react";

import { normKind, type TreeViewHints, type TreeViewSettings } from "@/assets/treeView";

export interface TreeViewChange {
  flattenKinds: ReadonlySet<string>;
  rootKinds: ReadonlySet<string> | null;
}

const Chip: React.FC<{
  on: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
  children: React.ReactNode;
}> = ({ on, disabled, title, onClick, children }) => (
  <button
    type="button"
    disabled={disabled}
    title={title}
    onClick={onClick}
    className={`px-1.5 py-0.5 rounded-sm text-[11px] border disabled:opacity-50 ${
      on ? "bg-blue-700 border-blue-500 text-white" : "border-gray-600 text-gray-300 hover:bg-gray-700"
    }`}
  >
    {children}
  </button>
);

function withToggled(set: ReadonlySet<string>, kind: string): Set<string> {
  const next = new Set(set);
  const k = normKind(kind);
  if (!next.delete(k)) next.add(k);
  return next;
}

const TreeViewPanel: React.FC<{
  settings: TreeViewSettings;
  hints: TreeViewHints | null;
  /** Kinds at the top of the tree as published, before anything is skipped. */
  topKinds: readonly string[];
  /** Kinds at the top as drawn, with how many branches each. */
  rootKindCensus: ReadonlyMap<string, number>;
  busy: boolean;
  error: string | null;
  onChange: (next: TreeViewChange) => void;
  onUseProviderDefaults: () => void;
}> = ({ settings, hints, topKinds, rootKindCensus, busy, error, onChange, onUseProviderDefaults }) => {
  // Offered: what is actually at the top, plus anything already chosen, so a
  // saved choice never disappears from the list that would undo it.
  const flattenOffer = [...new Set([...topKinds, ...settings.flattenKinds].map((k) => k.trim()).filter(Boolean))];
  const rootOffer = [...new Set([...rootKindCensus.keys(), ...(settings.rootKinds ?? [])].map((k) => k.trim()))];
  const hasOn = (set: ReadonlySet<string> | null, kind: string) => !!set && set.has(normKind(kind));

  return (
    <div className="px-1 py-1 space-y-1.5 border-b border-gray-700 text-xs">
      <div className="space-y-0.5">
        <div className="text-gray-400" title="Rows of a skipped kind are not drawn; their children take their place.">
          Start the tree below
        </div>
        <div className="flex flex-wrap gap-1">
          {flattenOffer.length === 0 && <span className="text-gray-500">nothing to skip</span>}
          {flattenOffer.map((kind) => (
            <Chip
              key={kind}
              on={hasOn(settings.flattenKinds, kind)}
              disabled={busy}
              title={`Skip every "${kind}" row and draw its children in its place`}
              onClick={() => onChange({ flattenKinds: withToggled(settings.flattenKinds, kind), rootKinds: settings.rootKinds })}
            >
              {kind || "(no kind)"}
            </Chip>
          ))}
        </div>
      </div>

      <div className="space-y-0.5">
        <div className="text-gray-400" title="Only these kinds are drawn at the top level. Off while searching.">
          Top-level kinds
        </div>
        <div className="flex flex-wrap gap-1">
          <Chip
            on={settings.rootKinds === null}
            disabled={busy}
            onClick={() => onChange({ flattenKinds: settings.flattenKinds, rootKinds: null })}
          >
            All
          </Chip>
          {rootOffer.map((kind) => (
            <Chip
              key={kind}
              on={hasOn(settings.rootKinds, kind)}
              disabled={busy}
              onClick={() => {
                const next = withToggled(settings.rootKinds ?? new Set(), kind);
                onChange({ flattenKinds: settings.flattenKinds, rootKinds: next.size ? next : null });
              }}
            >
              {kind || "(no kind)"}
              {rootKindCensus.has(kind) && <span className="ml-1 text-gray-400">{rootKindCensus.get(kind)}</span>}
            </Chip>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2 text-gray-500">
        <span className="flex-1">
          {settings.source === "saved"
            ? "Saved for everyone in this scope."
            : settings.source === "provider"
              ? "The provider's defaults."
              : "Drawn as published."}
        </span>
        {hints && settings.source === "saved" && (
          <button
            type="button"
            disabled={busy}
            className="px-1.5 py-0.5 rounded-sm border border-gray-600 hover:bg-gray-700 disabled:opacity-50"
            onClick={onUseProviderDefaults}
          >
            Use provider defaults
          </button>
        )}
      </div>
      {error && <div className="text-red-300 break-words">{error}</div>}
    </div>
  );
};

export default TreeViewPanel;
