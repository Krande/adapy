import React, {useState} from "react";

import type {
    DbAttributeDuplicates,
    DbDuplicateGroup,
    DbFoldResult,
    DbPruneRevisionsResult,
    DbPruneRevisionsSpec,
} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";
import {formatBytes} from "@/utils/format";

import {duplicateSummary, foldBlocker, narrowingSkips, shareLabel, type ShareTarget} from "./dbStorage";

// Admin -> Database -> Attribute index -> Duplicates: the (provider, collection)s indexed in more
// than one scope, one line per provider and scope pair. Two ways to get down to one copy, each a
// dry run first and a confirm that does exactly what was previewed:
//
// * Fold: keep the Shared copy, share it into the other scope (its own shares move to the Shared
//   copy). Only toward Shared -- everyone may read it -- so nothing is hidden from anyone.
// * Prune older revisions: keep the newest copy of each collection. Where the newest is in Shared
//   that is a fold; where it is only in a narrower scope (a project), dropping the older Shared copy
//   would hide the collection from everyone else -- those are listed apart and left alone unless
//   explicitly included, behind a second confirm. A project's rows are never shared on.
//
// The rules live in db/attribute_index.py (fold_attr_duplicates, prune_attr_revisions).

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));
const bytes = (n: number | null | undefined) => formatBytes(n, {compact: true});
const btn = "rounded-sm px-2 py-0.5 text-xs disabled:opacity-50";

const FoldPreview: React.FC<{result: DbFoldResult; targets: readonly ShareTarget[]}> = ({result, targets}) => (
    <div className="text-[11px] text-gray-300 space-y-0.5">
        <div>
            {result.dry_run ? "Would fold" : "Folded"} <b>{result.collections.length}</b> collection(s) of {result.provider}: keep
            the {shareLabel(result.keep_scope, targets)} copy, share it into {shareLabel(result.drop_scope, targets)} —
            {result.dry_run ? "would free" : "freed"} about <b>{bytes(result.bytes)}</b>.
        </div>
        <ul className="ml-3 text-gray-400 max-h-40 overflow-auto">
            {result.collections.map((c) => (
                <li key={c.collection}>
                    {c.collection}: {c.drop_revision ?? "—"} → {c.keep_revision ?? "—"}
                    {c.same_revision ? " (same revision)" : ""} · {bytes(c.bytes)}
                    {c.repointed.length ? ` · its shares into ${c.repointed.map((s) => shareLabel(s, targets)).join(", ")} move to the kept copy` : ""}
                </li>
            ))}
        </ul>
        {result.errors.map((e) => (
            <div key={e} className="text-red-300">{e}</div>
        ))}
    </div>
);

/** "Keep the Shared copy, share it into <scope>": preview, then confirm. */
const FoldAction: React.FC<{group: DbDuplicateGroup; targets: readonly ShareTarget[]; onDone: () => void}> = ({group, targets, onDone}) => {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [preview, setPreview] = useState<DbFoldResult | null>(null);
    const [done, setDone] = useState<DbFoldResult | null>(null);
    const blocker = foldBlocker(group);
    const run = async (dryRun: boolean) => {
        if (!group.keep_scope || !group.drop_scope) return;
        setBusy(true);
        setError(null);
        try {
            const r = await viewerApi.adminDbFoldAttr(group.provider, group.keep_scope, group.drop_scope, dryRun);
            if (dryRun) {
                setPreview(r);
                setDone(null);
            } else {
                setPreview(null);
                setDone(r);
                onDone();
            }
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    className={`${btn} bg-gray-700 hover:bg-gray-600`}
                    disabled={busy || blocker !== null}
                    title={blocker ?? "Preview the fold; nothing is changed"}
                    onClick={() => void run(true)}
                >
                    Keep the Shared copy, share it into {group.drop_scope ? shareLabel(group.drop_scope, targets) : "the other scope"}
                </button>
                {preview && preview.collections.length > 0 && (
                    <button type="button" className={`${btn} bg-red-700 hover:bg-red-600`} disabled={busy} onClick={() => void run(false)}>
                        Confirm: fold {preview.collections.length} collection(s), free {bytes(preview.bytes)}
                    </button>
                )}
                {preview && preview.collections.length === 0 && <span className="text-gray-500">nothing to fold</span>}
            </div>
            {blocker && <div className="text-[11px] text-gray-500">{blocker}</div>}
            {error && <div className="text-[11px] text-red-300">{error}</div>}
            {preview && <FoldPreview result={preview} targets={targets}/>}
            {done && <FoldPreview result={done} targets={targets}/>}
        </div>
    );
};

const RevisionsPreview: React.FC<{result: DbPruneRevisionsResult; targets: readonly ShareTarget[]}> = ({result, targets}) => {
    const label = (s: string) => shareLabel(s, targets);
    const acted = result.collections.filter((c) => c.dropped.length);
    const narrowing = narrowingSkips(result);
    return (
        <div className="text-[11px] text-gray-300 space-y-1">
            <div>
                {result.dry_run ? "Would drop" : "Dropped"} <b>{result.dropped}</b> older copy(ies), about <b>{bytes(result.bytes)}</b>
                {result.skipped ? <span className="text-amber-300"> · {result.skipped} left alone</span> : null}
            </div>
            {acted.length > 0 && (
                <ul className="ml-3 text-gray-400 max-h-40 overflow-auto">
                    {acted.map((c) => (
                        <li key={`${c.provider}/${c.collection}`}>
                            {c.provider} / {c.collection}: keep {label(c.kept.scope)} ({c.kept.revision ?? "—"}), drop{" "}
                            {c.dropped
                                .map((d) => `${label(d.scope)} (${d.revision ?? "—"}, ${bytes(d.bytes)}${d.action === "fold" ? ", shared back in" : ", not replaced"})`)
                                .join(", ")}
                        </li>
                    ))}
                </ul>
            )}
            {narrowing.length > 0 && (
                <div className="space-y-0.5">
                    <div className="text-amber-300">
                        Newest copy is only in a narrower scope — dropping the older copy would hide it from everyone else
                        who reads that scope. Re-sweep these into Shared instead:
                    </div>
                    <ul className="ml-3 text-gray-400 max-h-40 overflow-auto">
                        {narrowing.map((n) => (
                            <li key={`${n.provider}/${n.collection}/${n.scope}`}>
                                {n.provider} / {n.collection}: newest is only in {label(n.keptScope)}; the older {label(n.scope)} copy ({bytes(n.bytes)}) stays
                            </li>
                        ))}
                    </ul>
                </div>
            )}
            {result.errors.map((e) => (
                <div key={e} className="text-red-300">{e}</div>
            ))}
        </div>
    );
};

/** "Prune older revisions" for one spec (a group's provider and scope pair, or everything). */
const PruneRevisionsAction: React.FC<{spec: DbPruneRevisionsSpec; label: string; targets: readonly ShareTarget[]; onDone: () => void}> = ({
    spec,
    label,
    targets,
    onDone,
}) => {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [narrowing, setNarrowing] = useState(false);
    const [preview, setPreview] = useState<DbPruneRevisionsResult | null>(null);
    const [done, setDone] = useState<DbPruneRevisionsResult | null>(null);
    /** The second confirm a narrowing prune needs. */
    const [armed, setArmed] = useState(false);
    const current = preview && preview.include_narrowing === narrowing ? preview : null;
    const run = async (dryRun: boolean) => {
        setBusy(true);
        setError(null);
        try {
            const r = await viewerApi.adminDbPruneAttrRevisions({...spec, include_narrowing: narrowing}, dryRun);
            if (dryRun) {
                setPreview(r);
                setDone(null);
            } else {
                setPreview(null);
                setDone(r);
                onDone();
            }
            setArmed(false);
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };
    const hides = current ? current.collections.filter((c) => c.dropped.some((d) => d.action === "drop")).length : 0;
    return (
        <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    className={`${btn} bg-gray-700 hover:bg-gray-600`}
                    disabled={busy}
                    title="Keep only the newest copy of each collection; preview first, nothing is changed"
                    onClick={() => void run(true)}
                >
                    {label}
                </button>
                <label className="flex items-center gap-1 text-[11px] text-gray-400" title="Also drop older copies whose newest copy is in a narrower scope">
                    <input
                        type="checkbox"
                        checked={narrowing}
                        onChange={(e) => {
                            setNarrowing(e.target.checked);
                            setArmed(false);
                        }}
                    />
                    also where the newest copy is narrower
                </label>
                {current && current.dropped > 0 && !(narrowing && hides > 0) && (
                    <button type="button" className={`${btn} bg-red-700 hover:bg-red-600`} disabled={busy} onClick={() => void run(false)}>
                        Confirm: drop {current.dropped} older copy(ies), free {bytes(current.bytes)}
                    </button>
                )}
                {current && current.dropped > 0 && narrowing && hides > 0 && !armed && (
                    <button type="button" className={`${btn} bg-red-700 hover:bg-red-600`} disabled={busy} onClick={() => setArmed(true)}>
                        Confirm: drop {current.dropped} older copy(ies)…
                    </button>
                )}
                {current && armed && (
                    <button type="button" className={`${btn} bg-red-900 hover:bg-red-800 ring-1 ring-red-400`} disabled={busy} onClick={() => void run(false)}>
                        Yes — hide {hides} collection(s) from everyone who cannot open the scope that keeps them
                    </button>
                )}
                {current && current.dropped === 0 && <span className="text-gray-500">no older copy to drop</span>}
            </div>
            {error && <div className="text-[11px] text-red-300">{error}</div>}
            {current && <RevisionsPreview result={current} targets={targets}/>}
            {done && <RevisionsPreview result={done} targets={targets}/>}
        </div>
    );
};

const DuplicatesSection: React.FC<{duplicates: DbAttributeDuplicates; targets: readonly ShareTarget[]; onDone: () => void}> = ({
    duplicates,
    targets,
    onDone,
}) => {
    if (!duplicates.groups.length) return null;
    return (
        <section className="rounded-sm border border-gray-800 bg-gray-900/40 p-2 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
                <h4 className="text-xs font-semibold text-gray-300">Duplicates</h4>
                <span className="text-[11px] text-gray-500">The same collections indexed in more than one scope.</span>
            </div>
            <PruneRevisionsAction spec={{}} label="Prune older revisions (all duplicates)" targets={targets} onDone={onDone}/>
            {duplicates.groups.map((g) => (
                <div key={`${g.provider}|${g.scopes.join("|")}`} className="border-t border-gray-800 pt-1.5 space-y-1">
                    <div className="text-[11px] text-gray-200">{duplicateSummary(g, targets, bytes)}</div>
                    <div className="text-[11px] text-gray-500">
                        {g.scopes.map((s) => `${shareLabel(s, targets)} ${bytes(g.bytes[s])}, newer in ${g.newer_ingest[s] ?? 0}`).join(" · ")}
                    </div>
                    <FoldAction group={g} targets={targets} onDone={onDone}/>
                    <PruneRevisionsAction
                        spec={{provider: g.provider, scopes: [...g.scopes]}}
                        label="Prune older revisions"
                        targets={targets}
                        onDone={onDone}
                    />
                </div>
            ))}
        </section>
    );
};

export default DuplicatesSection;
