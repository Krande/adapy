import React from "react";

import { useEngineCatalogStore } from "@/state/engineCatalogStore";
import type { ProceduralEngineKind } from "@/services/viewerApi";

// Per-scope procedural-engine registry admin panel. Register pluggable engines
// (an external repo built into a pyodide wheel, or a server-only engine) the
// cellbuilder can compile with. The built-in "adapy default" engine is always
// present and read-only.

const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const btnDanger = "px-1.5 rounded-sm bg-red-700/70 text-white hover:bg-red-600";
const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 w-full";

const KINDS: ProceduralEngineKind[] = ["wheel", "server", "builtin"];

const ProceduralEngineAdminPanel: React.FC<{ embedded?: boolean }> = ({
  embedded = false,
}) => {
  const { engines, selectedId, draft, dirty, busy, error } =
    useEngineCatalogStore();
  const store = useEngineCatalogStore;
  const [newName, setNewName] = React.useState("");

  React.useEffect(() => {
    void store.getState().refresh();
  }, [store]);

  return (
    <div
      className={
        "flex flex-col gap-2 text-xs text-white px-2 pb-2 " +
        (embedded
          ? "max-w-[560px] mx-auto"
          : "bg-gray-900/80 rounded-md min-w-[300px] max-w-[380px] pointer-events-auto max-h-[80svh] overflow-y-auto")
      }
    >
      <div className="sticky top-0 z-10 -mx-2 px-2 pt-2 pb-1 flex items-center gap-2 bg-gray-900 border-b border-gray-700/60">
        {draft && (
          <button
            className="px-1 rounded-sm hover:bg-gray-500/40"
            title="Back to registry"
            onClick={() => void store.getState().select(null)}
          >
            ←
          </button>
        )}
        <span className="font-bold truncate">
          {draft ? draft.name : "Procedural engines"}
        </span>
        {!draft && (
          <button
            className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
            title="Refresh"
            onClick={() => void store.getState().refresh()}
          >
            ⟳
          </button>
        )}
      </div>

      {error && <div className="text-red-400">{error}</div>}

      {!draft && (
        <>
          <div className="flex gap-1">
            <input
              className={inputCls}
              placeholder="New engine name…"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newName.trim()) {
                  void store.getState().create(newName);
                  setNewName("");
                }
              }}
            />
            <button
              className={btn}
              disabled={busy || !newName.trim()}
              onClick={() => {
                void store.getState().create(newName);
                setNewName("");
              }}
            >
              Add
            </button>
          </div>

          <div className="flex flex-col gap-0.5 max-h-60 overflow-y-auto">
            {engines.length === 0 && (
              <div className="text-gray-500 italic">No engines.</div>
            )}
            {engines.map((e) => {
              const builtin = e.origin === "builtin";
              return (
                <div
                  key={e.id}
                  className={
                    "flex items-center gap-1 px-1.5 py-0.5 rounded-sm " +
                    (builtin ? "text-gray-300 " : "cursor-pointer ") +
                    (e.id === selectedId
                      ? "bg-blue-800/60"
                      : "hover:bg-gray-700/60")
                  }
                  onClick={() =>
                    builtin ? undefined : void store.getState().select(e.id)
                  }
                >
                  <span className="truncate flex-1">{e.name}</span>
                  <span
                    className={
                      "rounded-sm px-1 text-[10px] " +
                      (builtin
                        ? "bg-emerald-900/70 text-emerald-200"
                        : "bg-sky-900/70 text-sky-200")
                    }
                  >
                    {builtin ? "built-in" : "db"}
                  </span>
                  <span className="text-gray-400 font-mono text-[10px]">
                    {e.slug}
                  </span>
                  {!builtin && (
                    <button
                      className={btnDanger}
                      title="Delete"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        void store.getState().remove(e.id);
                      }}
                    >
                      🗑
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {draft && (
        <div className="flex flex-col gap-2 pt-1">
          <label className="flex flex-col gap-0.5">
            <span className="text-gray-400">Name</span>
            <input
              className={inputCls}
              value={draft.name}
              onChange={(e) =>
                store.getState().setMeta({ name: e.target.value })
              }
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-gray-400">Description</span>
            <input
              className={inputCls}
              value={draft.description ?? ""}
              onChange={(e) =>
                store.getState().setMeta({ description: e.target.value })
              }
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-gray-400">Kind</span>
            <select
              className={inputCls}
              value={draft.doc.kind}
              onChange={(e) =>
                store
                  .getState()
                  .setDoc({ kind: e.target.value as ProceduralEngineKind })
              }
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-gray-400">Repo URL</span>
            <input
              className={inputCls}
              placeholder="git@github.com:org/engine.git"
              value={draft.doc.repo_url ?? ""}
              onChange={(e) =>
                store.getState().setDoc({ repo_url: e.target.value || null })
              }
            />
          </label>
          <div className="grid grid-cols-2 gap-1">
            <label className="flex flex-col gap-0.5">
              <span className="text-gray-400">Git ref</span>
              <input
                className={inputCls}
                placeholder="main / v1.2.3"
                value={draft.doc.ref ?? ""}
                onChange={(e) =>
                  store.getState().setDoc({ ref: e.target.value || null })
                }
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-gray-400">Deploy-key secret</span>
              <input
                className={inputCls}
                placeholder="vault secret name"
                value={draft.doc.deploy_key_secret ?? ""}
                onChange={(e) =>
                  store
                    .getState()
                    .setDoc({ deploy_key_secret: e.target.value || null })
                }
              />
            </label>
          </div>
          <label className="flex flex-col gap-0.5">
            <span className="text-gray-400">Entrypoint (module:callable)</span>
            <input
              className={inputCls}
              placeholder="engine.compile:run"
              value={draft.doc.entrypoint ?? ""}
              onChange={(e) =>
                store.getState().setDoc({ entrypoint: e.target.value || null })
              }
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-gray-400">
              Pyodide deps (comma-separated)
            </span>
            <input
              className={inputCls}
              placeholder="shapely, scipy"
              value={draft.doc.pyodide_deps.join(", ")}
              onChange={(e) =>
                store.getState().setDoc({
                  pyodide_deps: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>

          <div className="flex items-center gap-2 border-t border-gray-700 pt-2">
            <span className="text-gray-400">rev {draft.revision}</span>
            {dirty && <span className="text-amber-400">unsaved</span>}
            <button
              className={btn + " ml-auto"}
              disabled={!dirty || busy}
              onClick={() => void store.getState().save()}
            >
              Save
            </button>
          </div>
          <p className="text-gray-500 text-[10px] leading-snug">
            The deploy key is never stored or sent to the browser — only the
            Vault secret name. A server build worker clones the repo and builds
            a pyodide wheel (Phase 2).
          </p>
        </div>
      )}
    </div>
  );
};

export default ProceduralEngineAdminPanel;
