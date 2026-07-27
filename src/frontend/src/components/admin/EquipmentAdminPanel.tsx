import React from "react";

import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
import type {
  CatalogPort,
  PortCategory,
  PortDirection,
} from "@/services/viewerApi";
import EquipmentPreview from "./EquipmentPreview";

// Per-scope equipment-type catalog admin panel. An admin authors reusable
// equipment archetypes (name/description/slug, bbox, mass, IFC element class
// and a nozzle/port list), optionally links a CAD asset (upload or copy from
// the scope) and infers the bbox + a preview from it. The catalog feeds the
// cellbuilder's add-equipment dropdown by slug. A live sidecar viewer shows the
// bbox + ports (and the CAD preview once inferred).

const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const btnGray =
  "px-2 py-1 rounded-sm bg-gray-600 text-white disabled:opacity-50 hover:bg-gray-500";
const btnDanger = "px-1.5 rounded-sm bg-red-700/70 text-white hover:bg-red-600";
const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 w-full";

const IFC_CLASSES = [
  "IfcBuildingElementProxy",
  "IfcPump",
  "IfcTank",
  "IfcValve",
  "IfcFan",
  "IfcCompressor",
  "IfcHeatExchanger",
  "IfcElectricGenerator",
  "IfcMotorConnection",
  "IfcAirTerminal",
];
const DIRECTIONS: PortDirection[] = ["IN", "OUT", "INOUT"];
const CATEGORIES: PortCategory[] = ["process", "electrical", "signal"];

const NumField: React.FC<{
  label: string;
  value: number;
  step?: number;
  onChange: (v: number) => void;
}> = ({ label, value, step = 0.1, onChange }) => (
  <label className="flex flex-col gap-0.5">
    <span className="text-gray-400">{label}</span>
    <input
      type="number"
      step={step}
      className={inputCls}
      value={Number.isFinite(value) ? value : 0}
      onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
    />
  </label>
);

const Vec3Field: React.FC<{
  label: string;
  value: [number, number, number];
  onChange: (v: [number, number, number]) => void;
}> = ({ label, value, onChange }) => (
  <div className="flex flex-col gap-0.5">
    <span className="text-gray-400">{label}</span>
    <div className="grid grid-cols-3 gap-1">
      {(["x", "y", "z"] as const).map((_, i) => (
        <input
          key={i}
          type="number"
          step={0.1}
          className={inputCls}
          value={value[i]}
          onChange={(e) => {
            const next = [...value] as [number, number, number];
            next[i] = parseFloat(e.target.value) || 0;
            onChange(next);
          }}
        />
      ))}
    </div>
  </div>
);

const PortEditor: React.FC<{ port: CatalogPort; index: number }> = ({
  port,
  index,
}) => {
  const updatePort = useEquipmentCatalogStore((s) => s.updatePort);
  const removePort = useEquipmentCatalogStore((s) => s.removePort);
  return (
    <div className="flex flex-col gap-1 p-1.5 rounded-sm bg-gray-800/70 border border-gray-700">
      <div className="flex items-center gap-1">
        <input
          className={inputCls}
          value={port.name}
          placeholder="port name"
          onChange={(e) => updatePort(index, { name: e.target.value })}
        />
        <button
          className={btnDanger}
          title="Remove port"
          onClick={() => removePort(index)}
        >
          ✕
        </button>
      </div>
      <div className="flex gap-1">
        <label className="flex flex-col gap-0.5 flex-1">
          <span className="text-gray-400">Direction</span>
          <select
            className={inputCls}
            value={port.direction}
            onChange={(e) =>
              updatePort(index, { direction: e.target.value as PortDirection })
            }
          >
            {DIRECTIONS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5 flex-1">
          <span className="text-gray-400">Category</span>
          <select
            className={inputCls}
            value={port.category}
            onChange={(e) =>
              updatePort(index, { category: e.target.value as PortCategory })
            }
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Vec3Field
        label="Position (local)"
        value={port.position}
        onChange={(v) => updatePort(index, { position: v })}
      />
      <Vec3Field
        label="Direction vector"
        value={port.direction_vector}
        onChange={(v) => updatePort(index, { direction_vector: v })}
      />
    </div>
  );
};

const EquipmentAdminPanel: React.FC = () => {
  const {
    equipmentTypes,
    availableEquipment,
    selectedEquipmentId,
    equipmentDraft: draft,
    equipmentDirty,
    equipmentBusy,
    equipmentError,
    bboxJob,
  } = useEquipmentCatalogStore();
  const store = useEquipmentCatalogStore;
  const [newName, setNewName] = React.useState("");
  const [copyKey, setCopyKey] = React.useState("");
  const fileRef = React.useRef<HTMLInputElement | null>(null);

  const scopeObj = useScopeStore((s) => s.current);
  const scopeStr = scopeObj ? scopeUrlPart(scopeObj) : "user:me";

  return (
    <div className="flex flex-col gap-2 text-xs text-white p-2 bg-gray-900/80 rounded-md min-w-[320px] max-w-[420px] pointer-events-auto max-h-[80vh] overflow-y-auto">
      <div className="flex items-center gap-2">
        <span className="font-bold">Equipment catalog</span>
        <button
          className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
          title="Refresh"
          onClick={() => void store.getState().refreshEquipment()}
        >
          ⟳
        </button>
        <button
          className="px-1 rounded-sm hover:bg-gray-500/40"
          title="Close"
          onClick={() => store.setState({ equipmentPanelOpen: false })}
        >
          ✕
        </button>
      </div>

      {equipmentError && <div className="text-red-400">{equipmentError}</div>}

      {/* create */}
      <div className="flex gap-1">
        <input
          className={inputCls}
          placeholder="New equipment type name…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newName.trim()) {
              void store.getState().createEquipment(newName);
              setNewName("");
            }
          }}
        />
        <button
          className={btn}
          disabled={equipmentBusy || !newName.trim()}
          onClick={() => {
            void store.getState().createEquipment(newName);
            setNewName("");
          }}
        >
          Add
        </button>
      </div>

      {/* list */}
      <div className="flex flex-col gap-0.5 max-h-40 overflow-y-auto">
        {equipmentTypes.length === 0 && availableEquipment.length === 0 && (
          <div className="text-gray-500 italic">
            No equipment types available.
          </div>
        )}
        {equipmentTypes.map((t) => (
          <div
            key={t.id}
            className={
              "flex items-center gap-1 px-1.5 py-0.5 rounded-sm cursor-pointer " +
              (t.id === selectedEquipmentId
                ? "bg-blue-800/60"
                : "hover:bg-gray-700/60")
            }
            onClick={() => void store.getState().selectEquipment(t.id)}
          >
            <span className="truncate flex-1">{t.name}</span>
            <span className="rounded-sm bg-sky-900/70 text-sky-200 px-1 text-[10px]">
              db
            </span>
            <span className="text-gray-400 font-mono text-[10px]">
              {t.slug}
            </span>
            {t.cad_key && (
              <span
                className="text-emerald-400 text-[10px]"
                title="has CAD asset"
              >
                CAD
              </span>
            )}
            <button
              className={btnDanger}
              title="Delete"
              onClick={(e) => {
                e.stopPropagation();
                void store.getState().deleteEquipment(t.id);
              }}
            >
              🗑
            </button>
          </div>
        ))}
        {availableEquipment.length > 0 && (
          <div className="mt-1 pt-1 border-t border-gray-700/60 text-[10px] uppercase text-gray-500">
            Built-in archetypes — sync to edit
          </div>
        )}
        {availableEquipment.map((t) => (
          <div
            key={t.slug}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-gray-300"
          >
            <span className="truncate flex-1">{t.name}</span>
            <span className="rounded-sm bg-gray-700 text-gray-300 px-1 text-[10px]">
              code
            </span>
            <span className="text-gray-500 font-mono text-[10px]">
              {t.slug}
            </span>
            <button
              className="px-1 rounded-sm text-sky-300 hover:bg-gray-600"
              title="Sync this built-in archetype into the DB catalog to edit it"
              disabled={equipmentBusy}
              onClick={() =>
                void store.getState().syncEquipmentFromCode(t.slug)
              }
            >
              ⤓DB
            </button>
          </div>
        ))}
      </div>

      {/* editor */}
      {draft && (
        <div className="flex flex-col gap-2 border-t border-gray-700 pt-2">
          <EquipmentPreview
            doc={draft.doc}
            scope={scopeStr}
            previewKey={draft.preview_glb_key ?? null}
          />

          <div className="flex flex-col gap-1">
            <label className="flex flex-col gap-0.5">
              <span className="text-gray-400">Name</span>
              <input
                className={inputCls}
                value={draft.name}
                onChange={(e) =>
                  store.getState().setEquipmentMeta({ name: e.target.value })
                }
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-gray-400">
                Slug (used by the cellbuilder)
              </span>
              <input
                className={inputCls}
                value={draft.slug}
                onChange={(e) =>
                  store.getState().setEquipmentMeta({ slug: e.target.value })
                }
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-gray-400">Description</span>
              <input
                className={inputCls}
                value={draft.description ?? ""}
                onChange={(e) =>
                  store
                    .getState()
                    .setEquipmentMeta({ description: e.target.value })
                }
              />
            </label>
          </div>

          <div className="grid grid-cols-3 gap-1">
            <NumField
              label="Lx [m]"
              value={draft.doc.bbox.lx}
              onChange={(v) =>
                store
                  .getState()
                  .setEquipmentDoc({ bbox: { ...draft.doc.bbox, lx: v } })
              }
            />
            <NumField
              label="Ly [m]"
              value={draft.doc.bbox.ly}
              onChange={(v) =>
                store
                  .getState()
                  .setEquipmentDoc({ bbox: { ...draft.doc.bbox, ly: v } })
              }
            />
            <NumField
              label="Lz [m]"
              value={draft.doc.bbox.lz}
              onChange={(v) =>
                store
                  .getState()
                  .setEquipmentDoc({ bbox: { ...draft.doc.bbox, lz: v } })
              }
            />
          </div>
          <div className="grid grid-cols-2 gap-1">
            <NumField
              label="Mass [kg]"
              value={draft.doc.mass}
              step={100}
              onChange={(v) => store.getState().setEquipmentDoc({ mass: v })}
            />
            <label className="flex flex-col gap-0.5">
              <span className="text-gray-400">IFC element class</span>
              <select
                className={inputCls}
                value={draft.doc.ifc_element_class}
                onChange={(e) =>
                  store
                    .getState()
                    .setEquipmentDoc({ ifc_element_class: e.target.value })
                }
              >
                {IFC_CLASSES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {/* ports */}
          <div className="flex items-center gap-2">
            <span className="font-semibold">Ports / nozzles</span>
            <button
              className={btnGray + " ml-auto text-[11px]"}
              onClick={() => store.getState().addPort()}
            >
              + Add port
            </button>
          </div>
          <div className="flex flex-col gap-1">
            {draft.doc.ports.map((p, i) => (
              <PortEditor key={i} port={p} index={i} />
            ))}
          </div>

          {/* CAD asset */}
          <div className="flex flex-col gap-1 border-t border-gray-700 pt-2">
            <span className="font-semibold">CAD asset</span>
            <div className="text-gray-400 font-mono text-[10px] truncate">
              {draft.cad_key ? draft.cad_key : "— none linked —"}
            </div>
            <div className="flex gap-1">
              <input
                ref={fileRef}
                type="file"
                hidden
                accept=".step,.stp,.ifc,.glb,.gltf,.stl,.obj,.sat,.xml"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void store.getState().uploadCad(f);
                  e.target.value = "";
                }}
              />
              <button
                className={btnGray}
                disabled={equipmentBusy}
                onClick={() => fileRef.current?.click()}
              >
                Upload CAD…
              </button>
              <button
                className={btn}
                disabled={
                  !draft.cad_key ||
                  bboxJob?.status === "queued" ||
                  bboxJob?.status === "running"
                }
                onClick={() => void store.getState().inferBbox()}
                title="Read the CAD asset, set the bbox and render a preview"
              >
                Infer bbox + preview
              </button>
            </div>
            <div className="flex gap-1">
              <input
                className={inputCls}
                placeholder="Copy scope file key (e.g. cad/pump.step)"
                value={copyKey}
                onChange={(e) => setCopyKey(e.target.value)}
              />
              <button
                className={btnGray}
                disabled={!copyKey.trim() || equipmentBusy}
                onClick={() => {
                  void store.getState().copyCadFromScope(copyKey);
                  setCopyKey("");
                }}
              >
                Copy
              </button>
            </div>
            {bboxJob && (
              <div
                className={
                  bboxJob.status === "error" ? "text-red-400" : "text-gray-400"
                }
              >
                bbox inference: {bboxJob.status}
                {bboxJob.error ? ` — ${bboxJob.error}` : ""}
              </div>
            )}
          </div>

          {/* save */}
          <div className="flex items-center gap-2 border-t border-gray-700 pt-2">
            <span className="text-gray-400">rev {draft.revision}</span>
            {equipmentDirty && <span className="text-amber-400">unsaved</span>}
            <button
              className={btn + " ml-auto"}
              disabled={!equipmentDirty || equipmentBusy}
              onClick={() => void store.getState().saveEquipment()}
            >
              Save
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default EquipmentAdminPanel;
