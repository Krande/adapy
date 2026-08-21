import React from "react";

import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
import type { SystemTemplateType } from "@/services/viewerApi";

// Per-scope system-template catalog admin panel. Reusable service-system
// definitions (a named CoolingWater piping system, a PowerFeed electrical
// system, ...) with category/type/medium/voltage and the routed-segment
// rendering knobs. Feeds the cellbuilder systems inspector.

const btn =
  "px-2 py-1 rounded-sm bg-accent text-white disabled:opacity-50 pointer-fine:hover:bg-accent";
const btnDanger = "px-1.5 rounded-sm bg-fail-subtle text-white pointer-fine:hover:bg-fail";
const inputCls =
  "text-content bg-surface-2 border border-edge rounded-sm px-1 py-0.5 w-full";

const TYPES: SystemTemplateType[] = ["piping", "duct", "cable", "electrical"];

const SystemAdminPanel: React.FC<{ embedded?: boolean }> = ({
  embedded = false,
}) => {
  const {
    systemTemplates,
    availableSystems,
    selectedSystemId,
    systemDraft: draft,
    systemDirty,
    systemBusy,
    systemError,
  } = useEquipmentCatalogStore();
  const store = useEquipmentCatalogStore;
  const [newName, setNewName] = React.useState("");

  return (
    <div
      className={
        "flex flex-col gap-2 text-xs text-white px-2 pb-2 " +
        (embedded
          ? // In the admin panel: fill a centred column and let the tab's
            // own scroll container handle overflow (no floating card).
            "max-w-[560px] mx-auto"
          : "bg-surface-0 rounded-md min-w-[300px] max-w-[380px] pointer-events-auto max-h-[80svh] overflow-y-auto")
      }
    >
      {/* Sticky header keeps Close (and Back, while editing) reachable no matter
          how long the editor scrolls — important on mobile where the panel fills
          the viewport. The scroll container has no top padding (the header owns
          the top) and the header is fully opaque so scrolled content never peeks
          above it. */}
      <div className="sticky top-0 z-10 -mx-2 px-2 pt-2 pb-1 flex items-center gap-2 bg-surface-0 border-b border-edge">
        {draft && (
          <button
            className="px-1 rounded-sm pointer-fine:hover:bg-surface-3"
            title="Back to catalog"
            onClick={() => void store.getState().selectSystem(null)}
          >
            ←
          </button>
        )}
        <span className="font-bold truncate">
          {draft ? draft.name : "System catalog"}
        </span>
        {!draft && (
          <button
            className="ml-auto px-1 rounded-sm pointer-fine:hover:bg-surface-3"
            title="Refresh"
            onClick={() => void store.getState().refreshSystems()}
          >
            ⟳
          </button>
        )}
        {!embedded && (
          <button
            className={
              (draft ? "ml-auto " : "") + "px-1 rounded-sm pointer-fine:hover:bg-surface-3"
            }
            title="Close"
            onClick={() => store.setState({ systemPanelOpen: false })}
          >
            ✕
          </button>
        )}
      </div>

      {systemError && <div className="text-fail">{systemError}</div>}

      {/* create + list — hidden while editing a template (master-detail) */}
      {!draft && (
      <>
      <div className="flex gap-1">
        <input
          className={inputCls}
          placeholder="New system template name…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newName.trim()) {
              void store.getState().createSystem(newName);
              setNewName("");
            }
          }}
        />
        <button
          className={btn}
          disabled={systemBusy || !newName.trim()}
          onClick={() => {
            void store.getState().createSystem(newName);
            setNewName("");
          }}
        >
          Add
        </button>
      </div>

      <div className="flex flex-col gap-0.5 max-h-40 overflow-y-auto">
        {systemTemplates.length === 0 && availableSystems.length === 0 && (
          <div className="text-content-subtle italic">No system types available.</div>
        )}
        {systemTemplates.map((t) => (
          <div
            key={t.id}
            className={
              "flex items-center gap-1 px-1.5 py-0.5 rounded-sm cursor-pointer " +
              (t.id === selectedSystemId
                ? "bg-accent-subtle"
                : "pointer-fine:hover:bg-surface-2")
            }
            onClick={() => void store.getState().selectSystem(t.id)}
          >
            <span className="truncate flex-1">{t.name}</span>
            <span className="rounded-sm bg-info-subtle text-info px-1 text-[10px]">
              db
            </span>
            <span className="text-content-muted font-mono text-[10px]">
              {t.slug}
            </span>
            <button
              className={btnDanger}
              title="Delete"
              onClick={(e) => {
                e.stopPropagation();
                void store.getState().deleteSystem(t.id);
              }}
            >
              🗑
            </button>
          </div>
        ))}
        {availableSystems.length > 0 && (
          <div className="mt-1 pt-1 border-t border-edge text-[10px] uppercase text-content-subtle">
            Built-in kinds — sync to edit
          </div>
        )}
        {availableSystems.map((t) => (
          <div
            key={t.slug}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-content"
          >
            <span className="truncate flex-1">{t.name}</span>
            <span className="rounded-sm bg-surface-2 text-content px-1 text-[10px]">
              code
            </span>
            <span className="text-content-subtle font-mono text-[10px]">
              {t.type}
            </span>
            <button
              className="px-1 rounded-sm text-info pointer-fine:hover:bg-surface-3"
              title="Sync this built-in kind into the DB catalog to edit it"
              disabled={systemBusy}
              onClick={() => void store.getState().syncSystemFromCode(t.slug)}
            >
              ⤓DB
            </button>
          </div>
        ))}
      </div>
      </>
      )}

      {draft && (
        <div className="flex flex-col gap-2 pt-1">
          <label className="flex flex-col gap-0.5">
            <span className="text-content-muted">Name</span>
            <input
              className={inputCls}
              value={draft.name}
              onChange={(e) =>
                store.getState().setSystemMeta({ name: e.target.value })
              }
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-content-muted">Description</span>
            <input
              className={inputCls}
              value={draft.description ?? ""}
              onChange={(e) =>
                store.getState().setSystemMeta({ description: e.target.value })
              }
            />
          </label>
          <div className="grid grid-cols-2 gap-1">
            <label className="flex flex-col gap-0.5">
              <span className="text-content-muted">Type</span>
              <select
                className={inputCls}
                value={draft.doc.type}
                onChange={(e) =>
                  store
                    .getState()
                    .setSystemDoc({
                      type: e.target.value as SystemTemplateType,
                    })
                }
              >
                {TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-content-muted">Medium</span>
              <input
                className={inputCls}
                value={draft.doc.medium ?? ""}
                placeholder="water, air, …"
                onChange={(e) =>
                  store
                    .getState()
                    .setSystemDoc({ medium: e.target.value || null })
                }
              />
            </label>
          </div>
          <div className="grid grid-cols-3 gap-1">
            <label className="flex flex-col gap-0.5">
              <span className="text-content-muted">Voltage [V]</span>
              <input
                type="number"
                step={10}
                className={inputCls}
                value={draft.doc.voltage ?? ""}
                disabled={draft.doc.type !== "electrical"}
                onChange={(e) =>
                  store
                    .getState()
                    .setSystemDoc({
                      voltage: e.target.value
                        ? parseInt(e.target.value, 10)
                        : null,
                    })
                }
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-content-muted">Pipe r [m]</span>
              <input
                type="number"
                step={0.01}
                className={inputCls}
                value={draft.doc.pipe_radius}
                onChange={(e) =>
                  store
                    .getState()
                    .setSystemDoc({
                      pipe_radius: parseFloat(e.target.value) || 0,
                    })
                }
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-content-muted">Pipe wt [m]</span>
              <input
                type="number"
                step={0.001}
                className={inputCls}
                value={draft.doc.pipe_wt}
                onChange={(e) =>
                  store
                    .getState()
                    .setSystemDoc({ pipe_wt: parseFloat(e.target.value) || 0 })
                }
              />
            </label>
          </div>

          <div className="flex items-center gap-2 border-t border-edge pt-2">
            <span className="text-content-muted">rev {draft.revision}</span>
            {systemDirty && <span className="text-warn">unsaved</span>}
            <button
              className={btn + " ml-auto"}
              disabled={!systemDirty || systemBusy}
              onClick={() => void store.getState().saveSystem()}
            >
              Save
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default SystemAdminPanel;
