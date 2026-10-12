// Attribute search: find nodes across the scope's collections by the attributes their providers
// indexed (`ada.assets/attribute-index@1`), and go from a hit to its row in the Sources tree.
//
// It spans every collection of every provider in the scope, so it is NOT on the toolbar of the
// tree's one collection: it fills its own floating, resizable window (`AttributeSearchWindow`),
// opened from the viewer's top bar or the Sources tab's launcher row, so the tree and the 3D view
// stay on screen beside it. Its filter area folds away to give the results the window. "Show in
// tree" opens the drawer on Sources and walks the tree to the hit (`revealSearchHit`); "Load" does
// the same and then loads the row the way its own "Load into scene" would.
//
// Provider-neutral: every key, kind and value offered comes from the server's index (or from what
// the user typed); nothing here names one provider's vocabulary.
//
// State lives in `useAttributeSearchStore`, so results survive closing the panel and an export can
// act on exactly the hits shown (`lastQuery`, `selectedHits()`).

import React, { useEffect, useMemo, useState } from "react";

import {
  FILTER_OPS,
  attrKeysOf,
  filterOptions,
  formatAttr,
  hitKey,
  newFilterRow,
  opTakesList,
  opTakesValue,
  partitionCounts,
  pickSummary,
  splitList,
  totalCount,
  type FilterOp,
  type FilterRow,
  type PickMode,
  type ScalarValue,
} from "@/assets/attributeSearch";
import { compactQuery } from "@/assets/searchExport";
import { useProviderName } from "@/state/providerNamesStore";
import {
  attributeSearchApi,
  type SearchCollection,
  type SearchHit,
  type SearchValueCount,
} from "@/services/api/attributeSearch";
import { getSingletonViewerStores } from "@/state/AdaViewerContext";
import { useAttributeSearchStore } from "@/state/attributeSearchStore";
import { scopeUrlPart } from "@/state/scopeStore";
import { useRunFocusStore } from "@/state/runFocusStore";
import { revealSearchHit } from "@/utils/tree_view/revealSearchHit";

import SearchExportDialog from "./SearchExportDialog";
import SearchExportRuns from "./SearchExportRuns";

const CONTROL = "h-7 rounded-md border border-gray-700 bg-gray-800 px-2 text-xs text-gray-100 placeholder:text-gray-500";
const BTN_PRIMARY =
  "h-7 px-3 rounded-md text-xs font-semibold bg-blue-400 text-gray-950 hover:bg-blue-300 disabled:opacity-50";
const BTN_SECONDARY =
  "h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50";
const BTN_ROW = "px-1.5 py-0.5 rounded text-[11px] text-blue-300 hover:text-white hover:bg-gray-700 disabled:opacity-50";
const LABEL = "text-[11px] uppercase tracking-wide text-gray-400";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The value text a suggestion is shown (and typed) as. */
const valueText = (v: ScalarValue) => (typeof v === "string" ? v : String(v));

/** The part of a value field that suggestions apply to: all of it, or the last item of a list. */
function activeToken(op: FilterOp, text: string): string {
  if (!opTakesList(op)) return text.trim();
  const i = text.lastIndexOf(",");
  return text.slice(i + 1).trim();
}

/** Remember the typed value of every item in `text` that names a suggestion exactly. */
function withTyped(row: FilterRow, text: string, suggestions: readonly SearchValueCount[]): FilterRow {
  const items = opTakesList(row.op) ? splitList(text) : [text.trim()];
  const typed: Record<string, ScalarValue> = { ...(row.typed ?? {}) };
  for (const item of items) {
    const hit = suggestions.find((s) => valueText(s.value) === item);
    if (hit) typed[item] = hit.value;
  }
  return { ...row, value: text, typed };
}

/** A provider / collection filter: "is any of" (only these) or "is none of" (every one but these),
 *  with a type-ahead over the options -- so leaving one of ninety-five out is one tick. Nothing
 *  ticked is every one, in either mode. */
const MultiPick: React.FC<{
  label: string;
  /** Singular, for the summary: "every collection". */
  noun: string;
  options: readonly { value: string; label: string; title?: string }[];
  chosen: readonly string[];
  mode: PickMode;
  onChange: (next: string[], mode: PickMode) => void;
}> = ({ label, noun, options, chosen, mode, onChange }) => {
  const [filter, setFilter] = useState("");
  const shown = useMemo(() => filterOptions(options, filter), [options, filter]);
  const labelOf = (v: string) => options.find((o) => o.value === v)?.label ?? v;
  const summary = pickSummary(chosen, mode, noun, labelOf);
  return (
    <details className="relative">
      <summary
        className={`${CONTROL} flex items-center gap-1 cursor-pointer list-none select-none min-w-[9rem] max-w-[16rem]`}
        aria-label={label}
        title={chosen.length ? `${mode === "exclude" ? "Every one but" : "Only"}: ${chosen.map(labelOf).join(", ")}` : undefined}
      >
        <span className="text-gray-400">{label}:</span>
        <span className={`truncate ${chosen.length ? (mode === "exclude" ? "text-amber-200" : "text-blue-200") : ""}`}>{summary}</span>
      </summary>
      <div className="absolute z-20 mt-1 w-72 max-w-[calc(100vw-2rem)] rounded-md border border-gray-700 bg-gray-900 p-1 shadow-xl space-y-1">
        <div className="flex items-center gap-1">
          <select
            aria-label={`${label} filter mode`}
            className={`${CONTROL} w-28`}
            value={mode}
            onChange={(e) => onChange([...chosen], e.target.value as PickMode)}
            title="Search only the ticked ones, or every one but the ticked ones"
          >
            <option value="include">is any of</option>
            <option value="exclude">is none of</option>
          </select>
          <input
            aria-label={`Find ${noun}`}
            className={`${CONTROL} flex-1 min-w-0`}
            placeholder="Type to find…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2 px-1 text-[11px]">
          <button
            type="button"
            className="text-blue-300 hover:text-white disabled:opacity-50"
            disabled={!shown.length}
            title={filter ? "Tick every one listed below" : "Tick every one"}
            onClick={() => onChange([...new Set([...chosen, ...shown.map((o) => o.value)])], mode)}
          >
            {filter ? "Tick shown" : "Tick all"}
          </button>
          <button type="button" className="text-blue-300 hover:text-white disabled:opacity-50" disabled={!chosen.length} onClick={() => onChange([], mode)}>
            Clear ({chosen.length})
          </button>
          <span className="ml-auto text-gray-500">{chosen.length ? summary : `every ${noun}`}</span>
        </div>
        <div className="max-h-56 overflow-auto">
          {shown.map((o) => (
            <label key={o.value} title={o.title} className="flex items-center gap-2 px-2 py-1 text-xs text-gray-100 hover:bg-gray-800 rounded cursor-pointer">
              <input
                type="checkbox"
                checked={chosen.includes(o.value)}
                onChange={(e) => onChange(e.target.checked ? [...chosen, o.value] : chosen.filter((c) => c !== o.value), mode)}
              />
              <span className="truncate">{o.label}</span>
            </label>
          ))}
          {!options.length && <div className="px-2 py-1 text-xs text-gray-500">Nothing indexed in this scope yet</div>}
          {options.length > 0 && !shown.length && <div className="px-2 py-1 text-xs text-gray-500">None matches “{filter}”</div>}
        </div>
      </div>
    </details>
  );
};

/** One filter row: key, operator, value -- key and value suggested by the index. */
const FilterRowEditor: React.FC<{
  row: FilterRow;
  scope: string;
  keysListId: string;
  partition: { provider?: string; collection?: string };
  onChange: (row: FilterRow) => void;
  onRemove: (() => void) | null;
  onSubmit: () => void;
}> = ({ row, scope, keysListId, partition, onChange, onRemove, onSubmit }) => {
  const [suggestions, setSuggestions] = useState<SearchValueCount[]>([]);
  const token = activeToken(row.op, row.value);
  const key = row.key.trim();
  useEffect(() => {
    if (!key) {
      setSuggestions([]);
      return;
    }
    const ctl = new AbortController();
    const t = setTimeout(() => {
      attributeSearchApi
        .searchValues(scope, key, { ...partition, prefix: token || undefined, limit: 50 }, ctl.signal)
        .then(setSuggestions)
        .catch(() => {
          // Suggestions only; a key with none (or a failed lookup) still searches.
        });
    }, 250);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [scope, key, token, partition.provider, partition.collection]);
  const listId = `attr-search-values-${row.id}`;
  // For a list, each suggestion completes the LAST item, keeping the ones before it.
  const head = opTakesList(row.op) && row.value.includes(",") ? row.value.slice(0, row.value.lastIndexOf(",") + 1) + " " : "";
  const takesValue = opTakesValue(row.op);
  return (
    <div className="flex items-center gap-1.5">
      <input
        aria-label="Attribute key"
        className={`${CONTROL} w-1/3 min-w-[6rem] max-w-[10rem]`}
        list={keysListId}
        placeholder="Key"
        value={row.key}
        onChange={(e) => onChange({ ...row, key: e.target.value, typed: undefined })}
      />
      <select
        aria-label="Operator"
        className={`${CONTROL} w-24 shrink-0`}
        value={row.op}
        onChange={(e) => onChange({ ...row, op: e.target.value as FilterOp })}
        title={FILTER_OPS.find((o) => o.id === row.op)?.title}
      >
        {FILTER_OPS.map((o) => (
          <option key={o.id} value={o.id} title={o.title}>
            {o.label}
          </option>
        ))}
      </select>
      {takesValue ? (
        <input
          aria-label="Value"
          className={`${CONTROL} flex-1 min-w-0`}
          list={listId}
          placeholder={opTakesList(row.op) ? "Values, comma separated" : "Value"}
          value={row.value}
          onChange={(e) => onChange(withTyped(row, e.target.value, suggestions))}
          onKeyDown={(e) => e.key === "Enter" && onSubmit()}
        />
      ) : (
        <span className="flex-1 min-w-0 truncate px-2 text-gray-500">{row.op === "exists" ? "any value" : "not set on the node"}</span>
      )}
      <datalist id={listId}>
        {takesValue && suggestions.map((s) => (
          <option key={valueText(s.value)} value={head + valueText(s.value)}>
            {`${valueText(s.value)} (${s.count})`}
          </option>
        ))}
      </datalist>
      <button
        type="button"
        className="h-7 w-7 shrink-0 rounded-md text-gray-400 hover:text-white hover:bg-gray-700 disabled:opacity-30"
        aria-label="Remove filter"
        title="Remove this filter"
        disabled={!onRemove}
        onClick={() => onRemove?.()}
      >
        ×
      </button>
    </div>
  );
};

type RowAction = { busy: boolean; note: string | null; error: boolean };

const FILTERS_OPEN_KEY = "ada.assets.attributeSearch.filtersOpen";

function readFiltersOpen(): boolean {
  try {
    return globalThis.localStorage?.getItem(FILTERS_OPEN_KEY) !== "0";
  } catch {
    return true;
  }
}

function writeFiltersOpen(open: boolean): void {
  try {
    globalThis.localStorage?.setItem(FILTERS_OPEN_KEY, open ? "1" : "0");
  } catch {
    // Remembered for this page only.
  }
}

const AttributeSearchPanel: React.FC = () => {
  const { useScopeStore } = getSingletonViewerStores();
  const scope = scopeUrlPart(useScopeStore((s) => s.current));
  const pn = useProviderName();

  const form = useAttributeSearchStore((s) => s.form);
  const results = useAttributeSearchStore((s) => s.results);
  const busy = useAttributeSearchStore((s) => s.busy);
  const error = useAttributeSearchStore((s) => s.error);
  const selected = useAttributeSearchStore((s) => s.selected);
  const resultScope = useAttributeSearchStore((s) => s.scope);
  const { setForm, run, loadMore, toggleSelected, selectAll, resetForm } = useAttributeSearchStore.getState();
  // The filter area folds away to give the results the window; the query stays summed up in a line.
  const [filtersOpen, setFiltersOpen] = useState(readFiltersOpen);
  const toggleFilters = () => {
    writeFiltersOpen(!filtersOpen);
    setFiltersOpen(!filtersOpen);
  };

  // What is indexed in this scope, and the keys in it (narrowed to one partition when exactly one
  // collection, or one provider, is chosen -- else the scope's).
  const [collections, setCollections] = useState<SearchCollection[]>([]);
  const [collectionsError, setCollectionsError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setCollectionsError(null);
    attributeSearchApi
      .searchCollections(scope)
      .then((c) => live && setCollections(c))
      .catch((e) => live && setCollectionsError(message(e)));
    return () => {
      live = false;
    };
  }, [scope]);
  const partition = useMemo(
    () => ({
      provider: form.providers.length === 1 && form.providerMode !== "exclude" ? form.providers[0] : undefined,
      collection: form.collections.length === 1 && form.collectionMode !== "exclude" ? form.collections[0] : undefined,
    }),
    [form.providers, form.collections, form.providerMode, form.collectionMode],
  );
  const [keys, setKeys] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    attributeSearchApi
      .searchKeys(scope, partition)
      .then((k) => live && setKeys(k))
      .catch(() => live && setKeys([]));
    return () => {
      live = false;
    };
  }, [scope, partition]);

  const collectionOptions = useMemo(() => {
    const by = new Map<string, SearchCollection[]>();
    for (const c of collections) by.set(c.collection, [...(by.get(c.collection) ?? []), c]);
    return [...by.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([name, parts]) => ({
        value: name,
        label: `${name} (${parts.reduce((n, p) => n + p.rows, 0).toLocaleString()})`,
        title: parts.map((p) => `${pn(p.provider)}: ${p.rows.toLocaleString()} nodes, revision ${p.revision ?? "?"}, ingested ${p.ingested_at ?? "?"}`).join("\n"),
      }));
  }, [collections, pn]);
  const providerOptions = useMemo(
    () => [...new Set(collections.map((c) => c.provider))].sort().map((p) => ({ value: p, label: pn(p), title: p })),
    [collections, pn],
  );

  const setRow = (id: string, row: FilterRow) => setForm({ rows: form.rows.map((r) => (r.id === id ? row : r)) });
  const submit = () => void run(scope);

  // Columns: what the user chose; offered from the keys listed and the hits' own attributes.
  const [columnDraft, setColumnDraft] = useState("");
  const columnOffers = useMemo(() => [...new Set([...keys, ...attrKeysOf(results?.hits ?? [])])].sort(), [keys, results]);
  const addColumn = (k: string) => {
    const key = k.trim();
    if (key && !form.columns.includes(key)) setForm({ columns: [...form.columns, key] });
    setColumnDraft("");
  };

  // Per hit: a reveal or load in flight, and what it said.
  const [actions, setActions] = useState<ReadonlyMap<string, RowAction>>(new Map());
  const patchAction = (k: string, a: RowAction) => setActions((cur) => new Map(cur).set(k, a));
  const reveal = async (hit: SearchHit, load: boolean) => {
    const k = hitKey(hit);
    patchAction(k, { busy: true, note: load ? "loading…" : "opening…", error: false });
    try {
      const out = await revealSearchHit(hit, { load });
      if (out.status === "superseded") {
        patchAction(k, { busy: false, note: null, error: false });
      } else if (out.status === "not-found") {
        patchAction(k, { busy: false, note: out.message, error: true });
      } else if (out.status === "ancestor") {
        patchAction(k, { busy: false, note: "only an ancestor is in the tree -- shown that instead", error: true });
      } else {
        patchAction(k, { busy: false, note: load ? "load started -- see the row in Sources" : null, error: false });
      }
    } catch (e) {
      patchAction(k, { busy: false, note: message(e), error: true });
    }
  };

  const hits = results?.hits ?? [];
  const counts = results ? partitionCounts(results.counts) : [];
  const total = results ? totalCount(results.counts) : 0;
  const allPicked = hits.length > 0 && hits.every((h) => selected.has(hitKey(h)));

  // Export: the dialog, and the runs list below the results. The list shows the searched scope's
  // runs -- or, sent here by the toast's "View run" on an export of another scope, that scope's.
  const lastQuery = useAttributeSearchStore((s) => s.lastQuery);
  const canExport = !!results && !!lastQuery && resultScope === scope && total > 0;
  const [exporting, setExporting] = useState(false);
  const [started, setStarted] = useState<{ id: string; n: number } | null>(null);
  const focus = useRunFocusStore((s) => s.focus);
  const [runsScope, setRunsScope] = useState(scope);
  useEffect(() => setRunsScope(scope), [scope]);
  useEffect(() => {
    if (focus?.kind === "export") setRunsScope(focus.scope);
  }, [focus]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-gray-900 text-gray-100 text-xs">
      {/* Not scrolled itself: that would clip the collection / provider pickers' drop-downs. A
          window too short for it folds the filters away instead. */}
      <div className="shrink-0 space-y-2 border-b border-gray-800 p-3">
        {filtersOpen && (
        <>
        <div className="flex flex-wrap items-center gap-2">
          <MultiPick
            label="Collections"
            noun="collection"
            options={collectionOptions}
            chosen={form.collections}
            mode={form.collectionMode}
            onChange={(c, m) => setForm({ collections: c, collectionMode: m })}
          />
          {(providerOptions.length > 1 || form.providers.length > 0) && (
            <MultiPick
              label="Providers"
              noun="provider"
              options={providerOptions}
              chosen={form.providers}
              mode={form.providerMode}
              onChange={(p, m) => setForm({ providers: p, providerMode: m })}
            />
          )}
          <input
            aria-label="Kinds"
            className={`${CONTROL} w-40`}
            placeholder="Kinds (comma separated)"
            title="Only nodes of these kinds, as the provider names them. Blank = any kind"
            value={form.kind}
            onChange={(e) => setForm({ kind: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <select
            aria-label="Depth"
            className={`${CONTROL} w-28`}
            value={form.depthMode}
            onChange={(e) => setForm({ depthMode: e.target.value as typeof form.depthMode })}
            title="The provider's depth, its top-level container being 1"
          >
            <option value="any">Any depth</option>
            <option value="eq">Depth =</option>
            <option value="range">Depth range</option>
          </select>
          {form.depthMode === "eq" && (
            <input aria-label="Depth equals" className={`${CONTROL} w-16`} inputMode="numeric" value={form.depthEq} onChange={(e) => setForm({ depthEq: e.target.value })} />
          )}
          {form.depthMode === "range" && (
            <>
              <input aria-label="Minimum depth" placeholder="min" className={`${CONTROL} w-16`} inputMode="numeric" value={form.depthMin} onChange={(e) => setForm({ depthMin: e.target.value })} />
              <input aria-label="Maximum depth" placeholder="max" className={`${CONTROL} w-16`} inputMode="numeric" value={form.depthMax} onChange={(e) => setForm({ depthMax: e.target.value })} />
            </>
          )}
        </div>
        {collectionsError && <div className="rounded-sm bg-red-900/60 px-2 py-1 text-red-100">Could not read the indexed collections: {collectionsError}</div>}

        <div className="space-y-1.5">
          <div className={LABEL}>Attributes</div>
          <datalist id="attr-search-keys">
            {keys.map((k) => (
              <option key={k} value={k} />
            ))}
          </datalist>
          {form.rows.map((row) => (
            <FilterRowEditor
              key={row.id}
              row={row}
              scope={scope}
              keysListId="attr-search-keys"
              partition={partition}
              onChange={(r) => setRow(row.id, r)}
              onRemove={form.rows.length > 1 ? () => setForm({ rows: form.rows.filter((r) => r.id !== row.id) }) : null}
              onSubmit={submit}
            />
          ))}
          <button type="button" className="text-blue-300 hover:text-white" onClick={() => setForm({ rows: [...form.rows, newFilterRow()] })}>
            + Add filter
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className={LABEL}>Columns</span>
          {form.columns.map((c) => (
            <span key={c} className="inline-flex items-center gap-1 rounded-full bg-gray-800 border border-gray-700 px-2 py-0.5">
              {c}
              <button type="button" aria-label={`Remove column ${c}`} className="text-gray-400 hover:text-white" onClick={() => setForm({ columns: form.columns.filter((x) => x !== c) })}>
                ×
              </button>
            </span>
          ))}
          <input
            aria-label="Add column"
            className={`${CONTROL} w-36`}
            list="attr-search-columns"
            placeholder="Add column…"
            title="An attribute to show as a column. With none chosen, every attribute is fetched (shown on hover)"
            value={columnDraft}
            onChange={(e) => {
              const v = e.target.value;
              // Picking from the list adds at once; typing waits for Enter.
              if (columnOffers.includes(v)) addColumn(v);
              else setColumnDraft(v);
            }}
            onKeyDown={(e) => e.key === "Enter" && addColumn(columnDraft)}
          />
          <datalist id="attr-search-columns">
            {columnOffers.filter((k) => !form.columns.includes(k)).map((k) => (
              <option key={k} value={k} />
            ))}
          </datalist>
        </div>
        </>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={BTN_SECONDARY}
            aria-expanded={filtersOpen}
            title={filtersOpen ? "Fold the filters away to give the results more room" : "Show the filters"}
            onClick={toggleFilters}
          >
            {filtersOpen ? "Hide filters ▴" : "Filters ▾"}
          </button>
          <button type="button" className={BTN_PRIMARY} disabled={busy} onClick={submit}>
            {busy && !results ? "Searching…" : "Search"}
          </button>
          {filtersOpen && (
            <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={resetForm} title="Clear every filter and column">
              Reset
            </button>
          )}
          {!filtersOpen && (
            <span className="min-w-0 flex-1 truncate text-gray-400" title={compactQuery(lastQuery)}>
              {lastQuery ? compactQuery(lastQuery) : "No search run yet"}
            </span>
          )}
          <span className="ml-auto" />
          {/* One model per hit into a chosen folder: the query the results answer and the picked
              hits, both from `useAttributeSearchStore`. Only once there are results of THIS scope
              -- an export of another scope's query would export from the wrong index. */}
          <button
            type="button"
            className={BTN_SECONDARY}
            disabled={!canExport}
            title={canExport ? "Write one IFC per hit into a folder" : "Search first -- the export takes the results' query"}
            onClick={() => setExporting(true)}
          >
            Export results…
          </button>
        </div>
        {error && <div className="rounded-sm bg-red-900/60 px-2 py-1 text-red-100">{error}</div>}
      </div>

      {results && (
        <div className="shrink-0 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-800 px-3 py-1.5 text-gray-300">
          <span>
            <b className="text-gray-100">{total.toLocaleString()}</b> {total === 1 ? "match" : "matches"}
            {hits.length < total && ` · ${hits.length.toLocaleString()} loaded`}
          </span>
          <span className="text-gray-500" title={`${results.pages} page(s), ${results.totalTookMs} ms on the server in all`}>
            {results.tookMs} ms
          </span>
          {counts.map((c) => (
            <span key={`${c.provider}/${c.collection}`} className="rounded-full bg-gray-800 px-2 py-0.5" title={`${pn(c.provider)} / ${c.collection}`}>
              {c.collection}
              {providerOptions.length > 1 ? ` · ${pn(c.provider)}` : ""}: {c.count.toLocaleString()}
            </span>
          ))}
          {resultScope && resultScope !== scope && <span className="text-amber-300">from scope {resultScope} -- search again for this one</span>}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-auto">
        {results && !hits.length && <div className="p-3 text-gray-400">Nothing matches.</div>}
        {hits.length > 0 && (
          <table className="w-full border-collapse">
            <thead className="sticky top-0 z-10 bg-gray-800 text-left text-gray-300">
              <tr>
                <th className="px-2 py-1 w-6">
                  <input type="checkbox" aria-label="Pick every loaded hit" checked={allPicked} onChange={(e) => selectAll(e.target.checked)} />
                </th>
                <th className="px-2 py-1 font-medium">Collection</th>
                <th className="px-2 py-1 font-medium">Full name</th>
                <th className="px-2 py-1 font-medium">Kind</th>
                <th className="px-2 py-1 font-medium text-right">Depth</th>
                {form.columns.map((c) => (
                  <th key={c} className="px-2 py-1 font-medium">
                    {c}
                  </th>
                ))}
                <th className="px-2 py-1" />
              </tr>
            </thead>
            <tbody>
              {hits.map((h) => {
                const k = hitKey(h);
                const act = actions.get(k);
                const attrsTitle = Object.entries(h.attrs ?? {})
                  .map(([a, v]) => `${a} = ${formatAttr(v)}`)
                  .join("\n");
                return (
                  <tr key={k} className={`border-b border-gray-800 hover:bg-gray-800/60 ${selected.has(k) ? "bg-blue-900/30" : ""}`}>
                    <td className="px-2 py-1">
                      <input type="checkbox" aria-label={`Pick ${h.full_name}`} checked={selected.has(k)} onChange={() => toggleSelected(k)} />
                    </td>
                    <td className="px-2 py-1 whitespace-nowrap" title={pn(h.provider)}>
                      {h.collection}
                    </td>
                    <td className="px-2 py-1 font-mono break-all" title={[h.source_ref ? `ref ${h.source_ref}` : "", attrsTitle].filter(Boolean).join("\n")}>
                      {h.full_name}
                    </td>
                    <td className="px-2 py-1 whitespace-nowrap">{h.kind ?? ""}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{h.depth ?? ""}</td>
                    {form.columns.map((c) => (
                      <td key={c} className="px-2 py-1">
                        {formatAttr(h.attrs?.[c])}
                      </td>
                    ))}
                    <td className="px-2 py-1 whitespace-nowrap text-right">
                      <button type="button" className={BTN_ROW} disabled={act?.busy} title="Open the Sources tree at this node and select it" onClick={() => void reveal(h, false)}>
                        Show in tree
                      </button>
                      <button
                        type="button"
                        className={BTN_ROW}
                        disabled={act?.busy}
                        title="Show it in the tree, then load its geometry as the row's own Load into scene would (where one provider has geometry for it; otherwise the row offers the choice)"
                        onClick={() => void reveal(h, true)}
                      >
                        Load
                      </button>
                      {act?.note && (
                        <div className={`max-w-[16rem] whitespace-normal text-[11px] ${act.error ? "text-amber-300" : "text-gray-400"}`}>{act.note}</div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {results?.nextCursor && (
          <div className="p-2 text-center">
            <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => void loadMore()}>
              {busy ? "Loading…" : `Load more (${(total - hits.length).toLocaleString()} left)`}
            </button>
          </div>
        )}
      </div>

      {runsScope !== scope && (
        <div className="shrink-0 border-t border-gray-800 px-3 py-1 text-amber-300">
          Export runs of scope {runsScope}{" "}
          <button type="button" className="text-blue-300 hover:text-white" onClick={() => setRunsScope(scope)}>
            show this scope's
          </button>
        </div>
      )}
      <SearchExportRuns scope={runsScope} started={started} />

      {exporting && lastQuery && (
        <SearchExportDialog
          scope={scope}
          query={lastQuery}
          picked={useAttributeSearchStore.getState().selectedHits()}
          total={total}
          onClose={() => setExporting(false)}
          onStarted={(run) => {
            setRunsScope(scope);
            setStarted((s) => ({ id: run.id, n: (s?.n ?? 0) + 1 }));
          }}
        />
      )}
    </div>
  );
};

export default AttributeSearchPanel;
