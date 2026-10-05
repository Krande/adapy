// The Sources tab's "Provider options" panel: the values a provider asks for that change what its
// requests for THIS collection fetch (`@/assets/providerOptions`), set once and sent with every
// request -- the tree and every node's geometry.
//
// Rendered from the provider's own declarations (`asset_request_options` naming entries of its
// `job_options`); where it declares a choices job, "List choices" runs it and a list option becomes
// checkboxes, each with the provider's description of what it adds. Core names no option.

import React, { useEffect, useState } from "react";

import { requestOptionChoices, type CollectionRequestDeps } from "@/assets/collectionRequest";
import { parseOptionChoices, type OptionChoice, type ProviderOptionValues } from "@/assets/providerOptions";
import type { PluginJobOption } from "@/components/admin/pluginOptionFields";
import type { AssetRequestOptions } from "@/services/assetScopeCollections";
import { readProviderOptions, saveProviderOptions } from "@/services/providerOptions";

/** One shared "nothing stored" value, so a section's draft is not reset on every render. */
const NOTHING_SET: ProviderOptionValues = Object.freeze({});

const BTN = "px-1.5 py-0.5 rounded-sm border border-gray-600 text-gray-200 hover:bg-gray-700 disabled:opacity-50";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function asList(v: unknown): string[] {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === "string");
  return typeof v === "string" && v.trim() ? [v.trim()] : [];
}

/** One provider's options for the collection: a draft, saved as a whole. */
const ProviderSection: React.FC<{
  scope: string;
  collection: string;
  providerId: string;
  declared: AssetRequestOptions;
  stored: ProviderOptionValues;
  deps: (onStage: (s: string) => void) => CollectionRequestDeps;
  onSaved: () => void;
}> = ({ scope, collection, providerId, declared, stored, deps, onSaved }) => {
  const [draft, setDraft] = useState<Record<string, unknown>>({ ...stored });
  const [choices, setChoices] = useState<Record<string, OptionChoice[]> | null>(null);
  const [listing, setListing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => setDraft({ ...stored }), [stored]);

  const dirty = JSON.stringify(draft) !== JSON.stringify(stored);
  const set = (name: string, value: unknown) => {
    setSaved(false);
    setDraft((d) => ({ ...d, [name]: value }));
  };

  const listChoices = async () => {
    if (!declared.choices) return;
    setListing("asking the provider…");
    setError(null);
    try {
      const summary = await requestOptionChoices(deps((s) => setListing(s)), scope, declared.choices, collection);
      setChoices(parseOptionChoices(summary));
    } catch (e) {
      setError(`Could not list the choices: ${message(e)}`);
    } finally {
      setListing(null);
    }
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      // Only what the provider declares now: anything else would arrive as an option it does
      // not know.
      const values = Object.fromEntries(declared.decls.map((d) => [d.name, draft[d.name]]));
      await saveProviderOptions(scope, collection, providerId, values);
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(`Could not save: ${message(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const field = (decl: PluginJobOption): React.ReactNode => {
    const value = draft[decl.name];
    if (decl.type === "string_list") {
      const list = asList(value);
      const offered = choices?.[decl.name];
      if (offered) {
        // Stored values the provider no longer offers stay visible, so unticking them is possible.
        const extra = list.filter((v) => !offered.some((c) => c.value === v)).map((v) => ({ value: v, label: v, description: "not offered now" }));
        return (
          <ul className="space-y-0.5 max-h-48 overflow-auto">
            {offered.length === 0 && extra.length === 0 && <li className="text-gray-500">The provider offers none for {collection.toUpperCase()}.</li>}
            {[...offered, ...extra].map((c) => (
              <li key={c.value}>
                <label className="flex items-start gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    className="mt-0.5 accent-blue-400"
                    checked={list.includes(c.value)}
                    onChange={(e) => set(decl.name, e.target.checked ? [...list, c.value] : list.filter((v) => v !== c.value))}
                  />
                  <span className="min-w-0">
                    <span className="text-gray-100 break-all">{c.label}</span>
                    {c.description && <span className="block text-gray-400 break-words">{c.description}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        );
      }
      return (
        <textarea
          aria-label={decl.title ?? decl.name}
          rows={Math.min(6, Math.max(2, list.length + 1))}
          placeholder={decl.placeholder ? `${decl.placeholder} — one per line` : "one per line"}
          className="w-full px-1.5 py-0.5 rounded-sm bg-gray-800 border border-gray-600 text-gray-100 font-mono text-[11px]"
          value={list.join("\n")}
          onChange={(e) => set(decl.name, e.target.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean))}
        />
      );
    }
    if (decl.type === "bool") {
      return (
        <input
          type="checkbox"
          className="accent-blue-400"
          checked={value === undefined ? decl.default === true : value === true}
          onChange={(e) => set(decl.name, e.target.checked)}
        />
      );
    }
    if (decl.type === "enum") {
      return (
        <select
          className="px-1 py-0.5 rounded-sm bg-gray-800 border border-gray-600 text-gray-100"
          value={typeof value === "string" ? value : ""}
          onChange={(e) => set(decl.name, e.target.value || undefined)}
        >
          <option value="">(provider default)</option>
          {(decl.enum ?? []).map((v) => (
            <option key={v} value={v}>
              {decl.labels?.[v] ?? v}
            </option>
          ))}
        </select>
      );
    }
    const numeric = decl.type === "int" || decl.type === "float";
    return (
      <input
        type={numeric ? "number" : "text"}
        placeholder={decl.placeholder}
        className="w-full px-1.5 py-0.5 rounded-sm bg-gray-800 border border-gray-600 text-gray-100"
        value={value === undefined || value === null ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (!numeric) set(decl.name, raw);
          else set(decl.name, raw === "" ? undefined : decl.type === "int" ? parseInt(raw, 10) : parseFloat(raw));
        }}
      />
    );
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className="font-medium text-gray-200 mr-auto">{providerId}</span>
        {declared.choices && (
          <button type="button" className={BTN} disabled={!!listing} onClick={() => void listChoices()} title={declared.choices.label}>
            {listing ? "Listing…" : choices ? "List again" : "List choices"}
          </button>
        )}
        <button type="button" className={BTN} disabled={busy || !dirty} onClick={() => void save()}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {listing && <div className="text-gray-400">{listing}</div>}
      {declared.decls.map((decl) => (
        <div key={decl.name} className="space-y-0.5">
          <div className="text-gray-300" title={decl.description}>
            {decl.title ?? decl.name}
          </div>
          {decl.description && <div className="text-gray-500">{decl.description}</div>}
          {field(decl)}
        </div>
      ))}
      {saved && !dirty && (
        <div className="text-emerald-300">Saved. Request the tree again for it to show what changed.</div>
      )}
      {error && <div className="text-red-300 break-words">{error}</div>}
    </div>
  );
};

const ProviderOptionsPanel: React.FC<{
  scope: string;
  collection: string;
  /** The providers that declare request options for this collection. */
  providers: ReadonlyMap<string, AssetRequestOptions>;
  deps: (onStage: (s: string) => void) => CollectionRequestDeps;
}> = ({ scope, collection, providers, deps }) => {
  const [stored, setStored] = useState<Record<string, ProviderOptionValues> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let live = true;
    setError(null);
    readProviderOptions(scope, collection)
      .then((doc) => live && setStored(doc.providers))
      .catch((e) => live && setError(`Could not read the provider options: ${message(e)}`));
    return () => {
      live = false;
    };
  }, [scope, collection, version]);

  return (
    <div className="px-1 py-1 space-y-2 border-b border-gray-700 text-xs">
      <div className="text-gray-400">
        Sent with every request for {collection.toUpperCase()} — its tree and every node's geometry — for everyone in this scope.
      </div>
      {providers.size === 0 && <div className="text-gray-500">No provider declares options for this collection.</div>}
      {error && <div className="text-red-300 break-words">{error}</div>}
      {stored &&
        [...providers.entries()]
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([providerId, declared]) => (
            <ProviderSection
              key={providerId}
              scope={scope}
              collection={collection}
              providerId={providerId}
              declared={declared}
              stored={stored[providerId] ?? NOTHING_SET}
              deps={deps}
              onSaved={() => setVersion((v) => v + 1)}
            />
          ))}
    </div>
  );
};

export default ProviderOptionsPanel;
