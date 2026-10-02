// Scene panel "Saved groups": name the current selection -- elements and levels, across every
// loaded model -- as a group everyone in the scope sees, and act on a group later: select it
// again, clash-check its members as one model, or ask a provider for their geometry.
//
// The sibling "Groups" section lists the groups a MODEL carries (its own design/FE sets). These
// are the viewer's own, stored per scope (`@/services/savedGroups`), and so they can span models.

import React, { useCallback, useEffect, useMemo, useState } from "react";

import { requestDeps } from "@/components/asset_browser/RequestCollection";
import { assetsApi } from "@/services/api/assets";
import { sourceNodesApi } from "@/services/api/sourceNodes";
import { assetProviderCollections, type AssetNodeRequest } from "@/services/assetScopeCollections";
import { groupsAreShared } from "@/services/savedGroups";
import { viewerApi } from "@/services/viewerApi";
import { loaderFor } from "@/state/assetBrowserLoader";
import { useAssetBrowserStore } from "@/state/assetBrowserStore";
import { useClashCheckStore } from "@/state/clashCheckStore";
import { useMeStore } from "@/state/meStore";
import { useSavedGroupsStore } from "@/state/savedGroupsStore";
import { useSceneInfoStore } from "@/state/sceneInfoStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { useSelectedObjectStore } from "@/state/useSelectedObjectStore";
import { resolveElementRows } from "@/utils/groups/groupAssetRows";
import { captureSelection, selectedRangeCount, selectSavedGroup } from "@/utils/groups/groupScene";
import { loadSavedGroup } from "@/utils/groups/groupLoad";
import { describeRequestOutcome, planNodeCount, requestGroupGeometry } from "@/utils/groups/groupRequest";
import { realDeliveryDeps } from "@/components/asset_browser/AssetsTab";
import { getSingletonViewerStores } from "@/state/AdaViewerContext";
import {
  describeMember,
  groupNodePlan,
  type GroupMember,
  type GroupNodePlan,
  type SavedGroup,
} from "@/utils/groups/savedGroups";

const BTN =
  "rounded-sm px-1.5 py-0.5 text-[11px] bg-gray-700 hover:bg-gray-600 text-gray-100 disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_PRIMARY =
  "rounded-sm px-2 py-0.5 text-[11px] bg-blue-700 hover:bg-blue-600 text-gray-100 disabled:opacity-50 disabled:cursor-not-allowed";

function listMembers(members: readonly GroupMember[], max = 4): string {
  const shown = members.slice(0, max).map(describeMember);
  return members.length > max ? `${shown.join("; ")}; and ${members.length - max} more` : shown.join("; ");
}

/** "Group selection…": capture, name, save. */
const CreateGroupForm: React.FC<{ scope: string; onDone: () => void }> = ({ scope, onDone }) => {
  const captured = useMemo(() => captureSelection(), []);
  const [name, setName] = useState("");
  const busy = useSavedGroupsStore((s) => s.busy);
  const create = useSavedGroupsStore((s) => s.create);
  const me = useMeStore((s) => s.displayName || s.email);

  const save = async () => {
    const group = await create(scope, name, captured.members, me);
    if (group) onDone();
  };

  return (
    <div className="flex flex-col gap-1 rounded-sm bg-gray-800/70 p-1.5 text-[11px]">
      {captured.members.length === 0 ? (
        <div className="text-amber-200">{captured.reason ?? "Nothing in the selection can be stored."}</div>
      ) : (
        <>
          <div className="text-gray-300">
            {captured.members.length} member{captured.members.length === 1 ? "" : "s"}:{" "}
            <span className="text-gray-400">{listMembers(captured.members)}</span>
          </div>
          {captured.skipped.length > 0 && (
            <div className="text-amber-200">
              Left out {captured.skipped.length}: {captured.skipped[0].name} — {captured.skipped[0].reason}
              {captured.skipped.length > 1 ? " (and others)" : ""}.
            </div>
          )}
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <input
              autoFocus
              type="text"
              aria-label="Group name"
              placeholder="Group name"
              className="flex-1 min-w-0 rounded-sm bg-gray-700 border border-gray-600 px-1 py-0.5 text-gray-100 text-base sm:text-[11px]"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Escape" && onDone()}
            />
            <button type="submit" className={BTN_PRIMARY} disabled={busy || !name.trim()}>
              {busy ? "saving…" : "Save"}
            </button>
          </form>
        </>
      )}
      <div className="flex justify-end">
        <button type="button" className={BTN} onClick={onDone}>
          Cancel
        </button>
      </div>
    </div>
  );
};

interface ProviderChoice {
  readonly providerId: string;
  readonly req: AssetNodeRequest;
}

/** "Request geometry…" for one group: which nodes its members name, and which providers to ask.
 *  Jobs go to the global toast, so a request outlives this form. */
const RequestGeometryForm: React.FC<{ scope: string; group: SavedGroup; onClose: () => void }> = ({
  scope,
  group: groupAtOpen,
  onClose,
}) => {
  // The group as it was when the form opened: a list refresh hands back new objects, and
  // resolving again for each would reset the form under the user.
  const [group] = useState(groupAtOpen);
  const isAdmin = useMeStore((s) => s.isAdmin);
  const [providers, setProviders] = useState<readonly ProviderChoice[] | null>(null);
  const [plan, setPlan] = useState<GroupNodePlan | null>(null);
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void (async () => {
      const [plugins, rows] = await Promise.all([
        viewerApi.listBackendPlugins().catch(() => ({ plugins: [] as unknown[] })),
        resolveElementRows(scope, group.members).catch(() => new Map<string, string>()),
      ]);
      if (!live) return;
      const choices: ProviderChoice[] = [];
      for (const p of assetProviderCollections(plugins.plugins ?? [])) {
        if (p.nodeRequest) choices.push({ providerId: p.providerId, req: p.nodeRequest });
      }
      setProviders(choices);
      setPicked(new Set(choices.filter((c) => !(c.req.requiresAdmin && !isAdmin)).slice(0, 1).map((c) => c.providerId)));
      setPlan(groupNodePlan(group.members, rows));
    })();
    return () => {
      live = false;
    };
  }, [scope, group, isAdmin]);

  const nodeCount = plan ? planNodeCount(plan) : 0;
  const chosen = (providers ?? []).filter((p) => picked.has(p.providerId) && !(p.req.requiresAdmin && !isAdmin));

  const run = useCallback(async () => {
    if (!plan || chosen.length === 0) return;
    setBusy(true);
    setError(null);
    setOutcome(null);
    const out = await requestGroupGeometry(requestDeps((s) => setStage(s)), scope, chosen, plan);
    setOutcome(describeRequestOutcome(out));
    if (out.failed.length) setError(`${out.failed.length} failed:\n${out.failed.join("\n")}`);
    setBusy(false);
    setStage(null);
    // The new publishes' claims reach the Sources tree, so its Load buttons enable.
    try {
      await loaderFor(useAssetBrowserStore, assetsApi, sourceNodesApi).refresh(scope);
    } catch {
      // The tree re-reads on its own next time it is opened.
    }
  }, [plan, chosen, scope]);

  return (
    <div className="mt-1 flex flex-col gap-1 rounded-sm bg-gray-800/70 p-1.5 text-[11px]">
      {providers === null || plan === null ? (
        <div className="text-gray-400">Finding the group's nodes in the published tree…</div>
      ) : (
        <>
          <div className="text-gray-300">
            {nodeCount} node{nodeCount === 1 ? "" : "s"} in {plan.byCollection.size} collection
            {plan.byCollection.size === 1 ? "" : "s"} can be requested.
          </div>
          {plan.unresolved.length > 0 && (
            <div className="text-amber-200" title={plan.unresolved.map((u) => `${describeMember(u.member)}: ${u.reason}`).join("\n")}>
              {plan.unresolved.length} member{plan.unresolved.length === 1 ? "" : "s"} cannot be requested:{" "}
              {describeMember(plan.unresolved[0].member)} — {plan.unresolved[0].reason}
              {plan.unresolved.length > 1 ? " (hover for all)" : ""}.
            </div>
          )}
          {providers.length === 0 ? (
            <div className="text-gray-400">No live provider takes node requests right now.</div>
          ) : (
            <div className="flex flex-col">
              <div className="text-gray-400">Request geometry from</div>
              {providers.map((p) => {
                const blocked = p.req.requiresAdmin && !isAdmin;
                return (
                  <label
                    key={p.providerId}
                    className={`flex items-center gap-1.5 ${blocked ? "opacity-50" : "cursor-pointer"}`}
                    title={blocked ? `Only an administrator can run ${p.req.pluginId}` : p.req.label}
                  >
                    <input
                      type="checkbox"
                      disabled={blocked || busy}
                      checked={picked.has(p.providerId) && !blocked}
                      onChange={() =>
                        setPicked((cur) => {
                          const next = new Set(cur);
                          if (!next.delete(p.providerId)) next.add(p.providerId);
                          return next;
                        })
                      }
                    />
                    <span className="truncate">{p.providerId}</span>
                    {p.req.onDemand && <span className="text-gray-500">(quick)</span>}
                  </label>
                );
              })}
            </div>
          )}
          {stage && <div className="text-gray-300 truncate">{stage}…</div>}
          {outcome && <div className="text-green-300">{outcome}</div>}
          {error && <div className="text-red-300 whitespace-pre-wrap break-words">{error}</div>}
        </>
      )}
      <div className="flex justify-end gap-1">
        <button type="button" className={BTN} onClick={onClose}>
          Close
        </button>
        <button
          type="button"
          className={BTN_PRIMARY}
          disabled={busy || !plan || nodeCount === 0 || chosen.length === 0}
          onClick={() => void run()}
        >
          {busy ? "requesting…" : `Request${chosen.length > 1 ? ` (${chosen.length} providers)` : ""}`}
        </button>
      </div>
    </div>
  );
};

const GroupRow: React.FC<{ scope: string; group: SavedGroup; shared: boolean }> = ({ scope, group, shared }) => {
  const busy = useSavedGroupsStore((s) => s.busy);
  const rename = useSavedGroupsStore((s) => s.rename);
  const remove = useSavedGroupsStore((s) => s.remove);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(group.name);
  const [requesting, setRequesting] = useState(false);
  const [note, setNote] = useState<{ tone: "info" | "warn"; text: string } | null>(null);
  const hasNodes = group.members.some((m) => m.target.kind === "node");

  const select = () => {
    const out = selectSavedGroup(group);
    const missing: string[] = [];
    if (out.notLoaded.length) missing.push(`not loaded: ${listMembers(out.notLoaded, 3)}`);
    if (out.notFound.length) missing.push(`not found in the loaded model: ${listMembers(out.notFound, 3)}`);
    setNote(
      missing.length
        ? { tone: "warn", text: `Selected ${out.ranges} object${out.ranges === 1 ? "" : "s"}; ${missing.join(" · ")}` }
        : { tone: "info", text: `Selected ${out.ranges} object${out.ranges === 1 ? "" : "s"}.` },
    );
  };

  const [loading, setLoading] = useState<string | null>(null);
  const loadIntoScene = () => {
    if (loading) return;
    setLoading("starting…");
    setNote(null);
    const deps = realDeliveryDeps((name) => getSingletonViewerStores().useModelState.getState().loadedSourceNames.has(name));
    void loadSavedGroup(group, scope, deps, setLoading)
      .then((out) => {
        const parts: string[] = [];
        parts.push(`${out.loaded} model${out.loaded === 1 ? "" : "s"} loaded${out.already ? `, ${out.already} already in the scene` : ""}`);
        parts.push(`${out.selection.ranges} object${out.selection.ranges === 1 ? "" : "s"} selected`);
        if (out.failed.length) {
          parts.push(
            `not loaded: ${out.failed.map((f) => `${listMembers(f.members, 2)} (${f.reason})`).join("; ")}`,
          );
        }
        setNote({ tone: out.failed.length || out.selection.notFound.length ? "warn" : "info", text: parts.join(" · ") });
      })
      .catch((e) => setNote({ tone: "warn", text: e instanceof Error ? e.message : String(e) }))
      .finally(() => setLoading(null));
  };

  const clashCheck = () => {
    useClashCheckStore.getState().setGroupTarget(group);
    const sceneInfo = useSceneInfoStore.getState();
    sceneInfo.setMode("clashes");
    sceneInfo.setShowSceneInfoBox(true);
  };

  return (
    <li className="py-1 border-b border-gray-700/60 last:border-b-0">
      <div className="flex items-center gap-1 min-w-0">
        {editing ? (
          <form
            className="flex flex-1 min-w-0 items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              void rename(scope, group.id, draft).then(() => setEditing(false));
            }}
          >
            <input
              autoFocus
              type="text"
              aria-label="New group name"
              className="flex-1 min-w-0 rounded-sm bg-gray-700 border border-gray-600 px-1 py-0.5 text-gray-100 text-base sm:text-[11px]"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setDraft(group.name);
                  setEditing(false);
                }
              }}
            />
            <button type="submit" className={BTN_PRIMARY} disabled={busy || !draft.trim() || draft.trim() === group.name}>
              Save
            </button>
          </form>
        ) : (
          <span
            className="flex-1 min-w-0 truncate text-xs"
            title={`${group.name}\n${group.members.map(describeMember).join("\n")}${group.created_by ? `\n\ncreated by ${group.created_by}` : ""}`}
          >
            {group.name}{" "}
            <span className="text-gray-400">
              ({group.members.length} member{group.members.length === 1 ? "" : "s"})
            </span>
          </span>
        )}
      </div>
      <div className="mt-0.5 flex flex-wrap items-center gap-1">
        <button type="button" className={BTN} onClick={select} title="Select this group's members in the loaded models">
          Select
        </button>
        <button
          type="button"
          className={BTN}
          disabled={!shared || !!loading}
          title={
            shared
              ? "Load the models this group's members live in -- from what is already published or stored, e.g. geometry requested in an earlier session -- then select the group"
              : "Loading published geometry needs the server, and this viewer is not connected to one"
          }
          onClick={loadIntoScene}
        >
          {loading ? "Loading…" : "Load into scene"}
        </button>
        <button
          type="button"
          className={BTN}
          disabled={!shared}
          title={
            shared
              ? "Check this group's members together, as one model, in the Clashes tab"
              : "A clash check runs on the server, and this viewer is not connected to one"
          }
          onClick={clashCheck}
        >
          Clash check
        </button>
        <button
          type="button"
          className={BTN}
          disabled={!shared || !hasNodes}
          title={
            !shared
              ? "Requests go to a provider through the server, and this viewer is not connected to one"
              : !hasNodes
                ? "No member of this group comes from a published node, so there is nothing to ask a provider for"
                : "Ask a provider for the geometry of this group's members, then publish it here"
          }
          onClick={() => setRequesting((v) => !v)}
        >
          Request geometry…
        </button>
        <button
          type="button"
          className={BTN}
          disabled={busy}
          onClick={() => {
            setDraft(group.name);
            setEditing((v) => !v);
          }}
        >
          Rename
        </button>
        <button
          type="button"
          className={`${BTN} hover:bg-red-800`}
          disabled={busy}
          onClick={() => {
            const who = shared ? " It is removed for everyone in this scope." : "";
            if (!window.confirm(`Delete the group "${group.name}"?${who}`)) return;
            void remove(scope, group.id);
          }}
        >
          Delete
        </button>
      </div>
      {loading && <div className="mt-0.5 text-[11px] text-gray-400">{loading}</div>}
      {note && (
        <div className={`mt-0.5 text-[11px] ${note.tone === "warn" ? "text-amber-200" : "text-gray-400"}`}>{note.text}</div>
      )}
      {requesting && <RequestGeometryForm scope={scope} group={group} onClose={() => setRequesting(false)} />}
    </li>
  );
};

const SavedGroupsSection: React.FC = () => {
  const scope = scopeUrlPart(useScopeStore((s) => s.current));
  const groups = useSavedGroupsStore((s) => s.groups);
  const loading = useSavedGroupsStore((s) => s.loading);
  const error = useSavedGroupsStore((s) => s.error);
  const load = useSavedGroupsStore((s) => s.load);
  const selected = useSelectedObjectStore((s) => s.selectedObjects);
  const [creating, setCreating] = useState(false);
  const shared = groupsAreShared();

  useEffect(() => {
    void load(scope);
  }, [scope, load]);

  const count = selectedRangeCount(selected);
  const sorted = useMemo(() => [...groups].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })), [groups]);

  return (
    <div className="flex flex-col gap-1 text-xs">
      <div className="flex items-center gap-1">
        <button
          type="button"
          className={BTN_PRIMARY}
          disabled={count === 0 || creating}
          title={
            count === 0
              ? "Select objects or levels first (in 3D or in the tree), from any of the loaded models"
              : "Name the current selection as a group everyone in this scope can use"
          }
          onClick={() => setCreating(true)}
        >
          Group selection…
        </button>
        <button type="button" className={BTN} disabled={loading} onClick={() => void load(scope)} title="Re-read the saved groups">
          {loading ? "reading…" : "Refresh"}
        </button>
        <span className="flex-1" />
        <span className="text-[10px] text-gray-500" title={shared ? undefined : "No server: groups are kept in this browser only"}>
          {shared ? "shared in this scope" : "this browser only"}
        </span>
      </div>
      {creating && <CreateGroupForm scope={scope} onDone={() => setCreating(false)} />}
      {error && <div className="text-[11px] text-red-300 break-words">{error}</div>}
      {sorted.length === 0 ? (
        !loading && <div className="text-[11px] text-gray-500">No saved groups yet.</div>
      ) : (
        <ul className="flex flex-col">
          {sorted.map((g) => (
            <GroupRow key={g.id} scope={scope} group={g} shared={shared} />
          ))}
        </ul>
      )}
    </div>
  );
};

export default SavedGroupsSection;
