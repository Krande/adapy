// The Selected Object panel's Results section: what has been found, built and simulated under the
// selection, read from the element links (`queryElementLinks`, `useElementLinks`) -- its own and its
// descendants'. Per connection (by ck): its spec and status, its welds, whether it has a simulation
// subject, and its newest simulation run, with the run's provider's own "open" (or "replay") button;
// per contact: the two members and what was measured. A link whose element has changed since
// (stale) or is no longer in its model (orphan) says so. The counts at the top are of every link
// under the selection; the list is read a page at a time, and follows a run that is going.

import React from "react";

import { useElementLinks } from "@/hooks/useElementLinks";
import { activeShellMode } from "@/layout/shellModes";
import { makePluginContext } from "@/plugins/context";
import { activateMode, modeOffered } from "@/plugins/pluginModes";
import type { SelectionSnapshot } from "@/plugins/propertyProviders";
import { openResultButtonLabel, resolveSimulationProviderUi } from "@/plugins/registry";
import { latestRun, type ElementLink, type ElementLinkGroup, type ElementLinksQuery } from "@/services/api/elementLinks";
import { simulationsApi } from "@/services/api/simulations";
import { modeToOpenIn } from "@/services/simulations/modeScope";
import { useViewerStores } from "@/state/AdaViewerContext";
import { useRunConsoleStore } from "@/state/runConsoleStore";
import { scopeUrlPart } from "@/state/scopeStore";
import { usePluginTheme } from "@/state/themeStore";

const PAGE = 20;
const DOT: Record<string, string> = {
  queued: "bg-gray-400",
  running: "bg-blue-400",
  done: "bg-green-400",
  error: "bg-red-400",
  cancelled: "bg-yellow-400",
  built: "bg-green-400",
  not_built: "bg-gray-500",
  failed: "bg-red-400",
};

/** The last name of an element key: the leaf of its name path, else its node id. */
export function keyLabel(key: string): string {
  const path = key.split("#p:")[1];
  const leaf = path ? path.split("/").pop() ?? path : key.split("#g:")[1] ?? key.split("/").pop() ?? key;
  return leaf.replace(/%2F/g, "/").replace(/%23/g, "#").replace(/%25/g, "%");
}

/** The counts line: contacts, connections, welds, subjects and runs by status. */
export function summaryParts(summary: {
  kinds: Partial<Record<string, number>>;
  sim_runs: Record<string, number>;
  stale: number;
  orphan: number;
}): string[] {
  const k = summary.kinds;
  const parts: string[] = [];
  const add = (n: number | undefined, one: string, many: string) => {
    if (n) parts.push(`${n} ${n === 1 ? one : many}`);
  };
  // A contact and a connection are a row per member: counted once each here.
  add(k.contact ? Math.ceil(k.contact / 2) : 0, "contact", "contacts");
  add(k.connection, "connection link", "connection links");
  add(k.weld, "weld link", "weld links");
  add(k.subject, "subject link", "subject links");
  const runs = Object.entries(summary.sim_runs)
    .filter(([, n]) => n)
    .map(([status, n]) => `${n} ${status}`);
  if (runs.length) parts.push(`runs: ${runs.join(", ")}`);
  if (summary.stale) parts.push(`${summary.stale} stale`);
  if (summary.orphan) parts.push(`${summary.orphan} orphaned`);
  return parts;
}

const Flags: React.FC<{ rows: readonly ElementLink[] }> = ({ rows }) => {
  const stale = rows.some((r) => r.stale);
  const orphan = rows.some((r) => r.orphan);
  if (!stale && !orphan) return null;
  return (
    <span className="flex gap-1 shrink-0">
      {stale && (
        <span className="rounded-sm bg-amber-700/60 px-1 text-[10px]" title="The element's geometry has changed since this was made" data-link-flag="stale">
          stale
        </span>
      )}
      {orphan && (
        <span className="rounded-sm bg-red-800/60 px-1 text-[10px]" title="The element is no longer in its model (renamed or removed)" data-link-flag="orphan">
          orphan
        </span>
      )}
    </span>
  );
};

const RunButton: React.FC<{ scope: string; link: ElementLink }> = ({ scope, link }) => {
  const stores = useViewerStores();
  const theme = usePluginTheme();
  const [error, setError] = React.useState<string | null>(null);
  const data = link.data as { run_id?: string; provider?: string; domain?: string; status?: string };
  const base = makePluginContext("", stores, theme);
  const providerRef = { id: String(data.provider ?? ""), domain: String(data.domain ?? "") };
  const ui = resolveSimulationProviderUi(base, providerRef);
  const opener = ui.openResult;
  if (!opener?.ui.openResult || !data.run_id) return null;
  const active = data.status === "queued" || data.status === "running";
  if (data.status === "queued") return null;
  const open = async () => {
    setError(null);
    try {
      const run = await simulationsApi.getRun(scope, String(data.run_id));
      const target = modeToOpenIn(ui.mode, activeShellMode(), modeOffered);
      if (target) activateMode(target, { reason: `open run ${run.run_id}` });
      await opener.ui.openResult!(makePluginContext(opener.pluginId, stores, theme), { run, scope, provider: null });
      useRunConsoleStore.getState().open(scope, run, run.provider.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <>
      <button
        type="button"
        className="shrink-0 rounded-sm bg-gray-700 px-1.5 text-[10px] text-white hover:bg-gray-600"
        onClick={() => void open()}
        title={error ?? undefined}
      >
        {openResultButtonLabel(opener.ui, active)}
      </button>
      {error && <span className="text-red-400 text-[10px] truncate">{error}</span>}
    </>
  );
};

const ConnectionRow: React.FC<{ scope: string; group: ElementLinkGroup }> = ({ scope, group }) => {
  const conns = group.rows.filter((r) => r.kind === "connection" && !r.superseded);
  const first = conns[0] ?? group.rows[0];
  const data = (first?.data ?? {}) as { spec?: string; status?: string };
  const members = [...new Set(conns.map((r) => keyLabel(r.element_key)))];
  const welds = new Set(group.rows.filter((r) => r.kind === "weld" && (r.data as { kept?: boolean }).kept !== false).map((r) => String((r.data as { weld_name?: string }).weld_name)));
  const subject = group.rows.some((r) => r.kind === "subject");
  const run = latestRun(group);
  const runStatus = run ? String((run.data as { status?: string }).status ?? "") : null;
  const status = String(data.status ?? "built");
  return (
    <li className="flex flex-col gap-0.5 rounded-sm bg-white/5 px-1 py-0.5" data-link-ck={group.ck ?? undefined}>
      <div className="flex items-center gap-1.5 min-w-0">
        <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[status] ?? "bg-gray-400"}`} title={status} aria-hidden />
        <span className="truncate font-medium" title={data.spec}>
          {data.spec || "connection"}
        </span>
        <span className="truncate text-gray-400" title={members.join(" / ")}>
          {members.join(" · ")}
        </span>
        <Flags rows={group.rows} />
      </div>
      <div className="ml-3.5 flex items-center gap-2 text-gray-400">
        {status !== "built" && <span>{status.replace("_", " ")}</span>}
        {welds.size > 0 && <span>{welds.size} weld{welds.size === 1 ? "" : "s"}</span>}
        <span title={subject ? "A simulation subject has been made of it" : "No simulation subject yet"}>{subject ? "subject ✓" : "no subject"}</span>
        {run && (
          <span className="flex items-center gap-1 min-w-0" data-link-run={String((run.data as { run_id?: string }).run_id)}>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[runStatus ?? ""] ?? "bg-gray-400"}`} aria-hidden />
            <span className="truncate">
              {String((run.data as { provider?: string }).provider ?? "run")} {runStatus}
            </span>
            <RunButton scope={scope} link={run} />
          </span>
        )}
      </div>
    </li>
  );
};

const ContactRow: React.FC<{ group: ElementLinkGroup }> = ({ group }) => {
  const row = group.rows.find((r) => !r.superseded) ?? group.rows[0];
  if (!row) return null;
  const d = row.data as { name?: string; other_name?: string; area?: number; distance?: number };
  const area = typeof d.area === "number" ? `${(d.area * 1e4).toFixed(1)} cm²` : "";
  const gap = typeof d.distance === "number" ? `${(Math.abs(d.distance) * 1000).toFixed(1)} mm ${d.distance < 0 ? "overlap" : "gap"}` : "";
  return (
    <li className="flex items-center gap-1.5 min-w-0 px-1" data-link-contact={group.group}>
      <span className="h-2 w-2 shrink-0 rounded-full border border-gray-400" aria-hidden />
      <span className="truncate">
        {d.name || keyLabel(row.element_key)} ↔ {d.other_name || keyLabel(String((row.data as { other_key?: string }).other_key ?? ""))}
      </span>
      <span className="truncate text-gray-400">{[area, gap].filter(Boolean).join(" · ")}</span>
      <Flags rows={group.rows} />
    </li>
  );
};

/** Rendered by the panel for a selection of nodes; nothing while they have no links. */
const ElementLinksSection: React.FC<{ sel: SelectionSnapshot }> = ({ sel }) => {
  const { useScopeStore } = useViewerStores();
  const scope = scopeUrlPart(useScopeStore((s) => s.current));
  const nodesKey = JSON.stringify(sel.nodes ?? null);
  const query = React.useMemo<ElementLinksQuery | null>(
    () => (sel.nodes?.length ? { nodes: sel.nodes, latest_only: true } : null),
    // The snapshot is a new object each render; its nodes are compared by content.
    [nodesKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const links = useElementLinks(scope, query, { limit: PAGE });
  if (!links.summary || links.summary.rows === 0) {
    return links.reindexing ? <div className="mt-2 text-[11px] text-gray-400">Results: indexing this scope…</div> : null;
  }
  const connections = links.groups.filter((g) => g.ck);
  const contacts = links.groups.filter((g) => !g.ck);
  return (
    <section className="mt-2 flex flex-col gap-1 text-[11px]" aria-label="Results" data-element-links="">
      <div className="flex items-center gap-2 text-[12px] text-gray-100">
        <span className="font-semibold">Results</span>
        <span className="truncate text-gray-400">{summaryParts(links.summary).join(" · ")}</span>
        {links.active && <span className="ml-auto text-blue-300">running…</span>}
      </div>
      {connections.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {connections.map((g) => (
            <ConnectionRow key={g.group} scope={scope} group={g} />
          ))}
        </ul>
      )}
      {contacts.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {contacts.map((g) => (
            <ContactRow key={g.group} group={g} />
          ))}
        </ul>
      )}
      {links.more && (
        <button type="button" className="self-start text-[var(--ada-accent)] hover:underline" disabled={links.loading} onClick={links.loadMore}>
          {links.loading ? "Loading…" : "More"}
        </button>
      )}
      {links.error && <div className="text-red-400 break-words">{links.error}</div>}
    </section>
  );
};

export default ElementLinksSection;
