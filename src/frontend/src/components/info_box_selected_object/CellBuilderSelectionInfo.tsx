import React from "react";

import {
  useCellBuilderStore,
  type BuilderCell,
  type BuilderSelection,
} from "@/state/cellBuilderStore";
import { axisLabel, BOX_FACE_SIDES } from "@/utils/cellbuilder/snap";
import { bandFaceIds, type LoftBand } from "@/utils/cellbuilder/loft";
import {
  addMetadataKey,
  asMetaObject,
  formatMetadataValue,
  metaOrNull,
  removeMetadataKey,
  renameMetadataKey,
  setMetadataValue,
  type MetaMap,
} from "@/utils/cellbuilder/metadata";

// The selected cell/equipment detail shown in the Selected Object Info panel:
// gizmo toggles, the geometry/parameter editors mirrored from the ada.topology
// pydantic entities, and the connected-systems list, each collapsible. Shows
// only while a procedural model has a selection. Model-wide actions (undo/redo,
// commit/compile, visibility toggles) live solely in the dedicated cellbuilder
// tool panel (CellBuilderPanel), not here.

const btn =
  "px-2 py-1 rounded-sm bg-accent text-white disabled:opacity-50 hover:bg-accent";
const inputCls =
  "text-content bg-surface-2 border border-edge rounded-sm px-1 py-0.5";

// Editable per-cell parameters, mirrored from ada/topology/entities.py
// (TopoSpace / TopoEquipment). Anything not listed still round-trips
// untouched through BuilderCell.params.
type ParamField =
  | { key: string; label: string; type: "bool" }
  | { key: string; label: string; type: "number"; step?: number }
  | { key: string; label: string; type: "text" }
  | { key: string; label: string; type: "select"; options: string[] };

const SPACE_PARAMS: ParamField[] = [
  { key: "AREA", label: "Area", type: "text" },
  { key: "PRIORITY", label: "Priority", type: "number", step: 1 },
  { key: "FLIP_FLOOR", label: "Flip floor", type: "bool" },
  { key: "GRID_X_CREATE", label: "Grid X", type: "bool" },
  { key: "GRID_Y_CREATE", label: "Grid Y", type: "bool" },
  { key: "GRID_Z_CREATE", label: "Grid Z", type: "bool" },
  { key: "SWITCH_BM_DIR_VERTICAL", label: "Switch vert. beams", type: "bool" },
  {
    key: "SWITCH_BM_DIR_HORIZONTAL",
    label: "Switch horiz. beams",
    type: "bool",
  },
];

const EQUIPMENT_PARAMS: ParamField[] = [
  {
    key: "SPACE_LOC",
    label: "Location",
    type: "select",
    options: ["FLOOR", "ROOF"],
  },
  { key: "massDry", label: "Mass dry [kg]", type: "number", step: 100 },
  { key: "massCont", label: "Mass content [kg]", type: "number", step: 100 },
  { key: "COGx", label: "COG x", type: "number", step: 0.1 },
  { key: "COGy", label: "COG y", type: "number", step: 0.1 },
  { key: "COGz", label: "COG z", type: "number", step: 0.1 },
];

const SYSTEM_TYPE_COLOR: Record<string, string> = {
  piping: "#38bdf8",
  duct: "#a3e635",
  cable: "#c084fc",
  electrical: "#f59e0b",
};

const ParamRow: React.FC<{ cell: BuilderCell; field: ParamField }> = ({
  cell,
  field,
}) => {
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const value = cell.params[field.key];
  if (field.type === "bool") {
    return (
      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => setCellParam(cell.id, field.key, e.target.checked)}
        />
        {field.label}
      </label>
    );
  }
  if (field.type === "select") {
    return (
      <label className="flex items-center gap-1">
        <span className="text-content">{field.label}</span>
        <select
          className={inputCls}
          value={typeof value === "string" ? value : ""}
          onChange={(e) =>
            setCellParam(cell.id, field.key, e.target.value || null)
          }
        >
          <option value=""></option>
          {field.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (field.type === "number") {
    return (
      <label className="flex items-center gap-1">
        <span className="text-content">{field.label}</span>
        <input
          type="number"
          step={field.step ?? 0.1}
          className={`${inputCls} w-20`}
          value={typeof value === "number" ? value : ""}
          onChange={(e) =>
            setCellParam(
              cell.id,
              field.key,
              e.target.value === "" ? null : Number(e.target.value),
            )
          }
        />
      </label>
    );
  }
  return (
    <label className="flex items-center gap-1">
      <span className="text-content">{field.label}</span>
      <input
        type="text"
        className={`${inputCls} w-24`}
        value={typeof value === "string" ? value : ""}
        onChange={(e) =>
          setCellParam(cell.id, field.key, e.target.value || null)
        }
      />
    </label>
  );
};

// Systems this equipment participates in — which system it's connected to, via
// which port. Its own collapsible section.
const PORT_CATEGORY_COLOR: Record<string, string> = {
  process: "#38bdf8",
  electrical: "#f59e0b",
  signal: "#ec4899",
};

const EquipmentSystems: React.FC<{
  equipmentName: string;
  equipmentType?: string;
}> = ({ equipmentName, equipmentType }) => {
  const systems = useCellBuilderStore((st) => st.systems);
  const equipmentTypes = useCellBuilderStore((st) => st.equipmentTypes);
  const [open, setOpen] = React.useState(true);
  const connected = Object.values(systems).filter((sys) =>
    sys.connections.some((c) => c.equipment === equipmentName),
  );

  // This equipment's type ports, and which are wired up by a system — so the
  // unconnected I/O (what the red "!" overlay flags) can be listed explicitly.
  const ports = React.useMemo(() => {
    if (!equipmentType) return [];
    const key = equipmentType.toLowerCase();
    const t =
      equipmentTypes.find((o) => o.slug.toLowerCase() === key) ??
      equipmentTypes.find((o) => o.name.toLowerCase() === key);
    return t?.ports ?? [];
  }, [equipmentType, equipmentTypes]);
  const connectedPorts = new Set<string>();
  for (const sys of Object.values(systems))
    for (const c of sys.connections)
      if (c.equipment === equipmentName && c.port) connectedPorts.add(c.port);
  const unconnected = ports.filter((p) => !connectedPorts.has(p.name));

  return (
    <div className="border-t border-edge pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-surface-2 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold">Systems & I/O</span>
        <span className="text-content-muted">({connected.length} wired)</span>
        {unconnected.some((p) => p.direction === "IN") && (
          <span className="ml-1 text-fail" title="Has unconnected input(s)">
            ⚠
          </span>
        )}
      </button>
      {open && (
        <div className="flex flex-col gap-1 px-1 pt-1">
          {connected.length === 0 ? (
            <div className="text-content-subtle italic">
              Not connected to any system.
            </div>
          ) : (
            <div className="flex flex-col gap-0.5">
              {connected.map((sys) => {
                const cports = sys.connections
                  .filter((c) => c.equipment === equipmentName)
                  .map((c) => c.port);
                return (
                  <div key={sys.id} className="flex items-center gap-1">
                    <span
                      className="inline-block w-2 h-2 rounded-full"
                      style={{ background: SYSTEM_TYPE_COLOR[sys.type] }}
                    />
                    <span className="truncate">{sys.name}</span>
                    <span className="text-content-muted">({sys.type})</span>
                    <span className="ml-auto text-content-muted">
                      {cports.join(", ")}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {/* Unconnected ports — inputs (IN) are the "missing" ones the overlay
              warns about; outputs/signals are shown too but not flagged. */}
          {unconnected.length > 0 && (
            <div className="flex flex-col gap-0.5 border-t border-edge pt-1">
              <span className="text-content-muted">Unconnected I/O</span>
              {unconnected.map((p) => (
                <div key={p.name} className="flex items-center gap-1">
                  <span
                    className="inline-block w-2 h-2 rounded-full"
                    style={{ background: PORT_CATEGORY_COLOR[p.category] }}
                  />
                  <span
                    className={
                      "truncate " + (p.direction === "IN" ? "text-fail" : "")
                    }
                  >
                    {p.name}
                  </span>
                  <span className="ml-auto text-content-subtle">
                    {p.direction} · {p.category}
                    {p.direction === "IN" ? " · missing" : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
          {equipmentType && ports.length === 0 && (
            <div className="text-content-subtle italic">
              Port list unavailable (type not loaded).
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// One editable station param bound to setLoftStationParam. Controlled by the
// live station value (regenerated on every edit); WIDTH/HEIGHT/RADIUS clamp to
// >= 0 in the store.
const LoftNumberField: React.FC<{
  label: string;
  member: string;
  stationIndex: number;
  paramKey: string;
  value: number;
  min?: number;
}> = ({ label, member, stationIndex, paramKey, value, min }) => {
  const setLoftStationParam = useCellBuilderStore((s) => s.setLoftStationParam);
  return (
    <label className="flex items-center gap-1">
      <span className="text-content-muted w-12">{label}</span>
      <input
        type="number"
        step={0.1}
        min={min}
        className={`${inputCls} w-20`}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v))
            setLoftStationParam(member, stationIndex, paramKey, v);
        }}
      />
    </label>
  );
};

// One station's editable params (position Z/X/Y + the section dims for its
// TYPE). SEGMENTS/TYPE are left as authored (shape family stays put in 3a).
const EditableStation: React.FC<{
  label: string;
  member: string;
  stationIndex: number;
  station: import("@/utils/cellbuilder/loft").LoftStation;
}> = ({ label, member, stationIndex, station }) => (
  <div className="flex flex-col gap-0.5 border border-edge rounded-sm p-1">
    <div className="flex items-center gap-1">
      <span className="text-content font-medium">{label}</span>
      <span className="text-content-subtle">({station.TYPE})</span>
    </div>
    <div className="flex flex-wrap gap-x-3 gap-y-0.5">
      <LoftNumberField
        label="Z"
        member={member}
        stationIndex={stationIndex}
        paramKey="Z"
        value={Number(station.Z ?? 0)}
      />
      <LoftNumberField
        label="X"
        member={member}
        stationIndex={stationIndex}
        paramKey="X"
        value={Number(station.X ?? 0)}
      />
      <LoftNumberField
        label="Y"
        member={member}
        stationIndex={stationIndex}
        paramKey="Y"
        value={Number(station.Y ?? 0)}
      />
      {station.TYPE === "circle" ? (
        <LoftNumberField
          label="Radius"
          member={member}
          stationIndex={stationIndex}
          paramKey="RADIUS"
          value={Number(station.RADIUS ?? 0)}
          min={0}
        />
      ) : (
        <>
          <LoftNumberField
            label="Width"
            member={member}
            stationIndex={stationIndex}
            paramKey="WIDTH"
            value={Number(station.WIDTH ?? 0)}
            min={0}
          />
          <LoftNumberField
            label="Height"
            member={member}
            stationIndex={stationIndex}
            paramKey="HEIGHT"
            value={Number(station.HEIGHT ?? 0)}
            min={0}
          />
        </>
      )}
    </div>
  </div>
);

// The band's per-face list (Phase 3b): each side panel + end cap with an
// EXCLUDE checkbox bound to setLoftFaceExcluded. Checked = the face's
// member-relative id is in the member's EXCLUDE_FACES, i.e. its plate is dropped
// on recompile. The row order matches the 3D proxy's material groups
// (edge0..edgeN, cap_lo, cap_hi) so a face picked in the scene highlights its
// row. Mirrors the box SE{n} side-exclude idiom, but keyed by loft face id.
const LoftFaces: React.FC<{
  band: LoftBand;
  pickedFaceIndex?: number;
}> = ({ band, pickedFaceIndex }) => {
  const setLoftFaceExcluded = useCellBuilderStore((s) => s.setLoftFaceExcluded);
  const [open, setOpen] = React.useState(true);
  const { edges, caps } = React.useMemo(() => bandFaceIds(band), [band]);
  // Flat row order MUST match the swept-band material groups in
  // CellBuilderController (side panels, then cap_lo, cap_hi) so a scene pick's
  // faceIndex resolves to the right row.
  const rows = React.useMemo(
    () => [
      ...edges.map((id, k) => ({ id, label: `Side ${k}` })),
      { id: caps[0], label: "Bottom cap (cap_lo)" },
      { id: caps[1], label: "Top cap (cap_hi)" },
    ],
    [edges, caps],
  );
  const excluded = new Set(band.excludeFaces);
  const pickedId =
    pickedFaceIndex !== undefined && pickedFaceIndex < rows.length
      ? rows[pickedFaceIndex].id
      : undefined;
  return (
    <div className="border border-edge rounded-sm p-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-surface-2 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold">Faces</span>
        <span className="text-content-muted">({excluded.size} excluded)</span>
      </button>
      {open && (
        <div className="flex flex-col gap-0.5 px-1 pt-1">
          {rows.map((row) => (
            <label
              key={row.id}
              className={
                "flex items-center gap-1 rounded-sm px-0.5 " +
                (row.id === pickedId ? "bg-fail-subtle" : "")
              }
              title={`Loft face ${band.member}:${row.id} — exclude to omit its plate at build`}
            >
              <input
                type="checkbox"
                checked={excluded.has(row.id)}
                onChange={(e) =>
                  setLoftFaceExcluded(band.member, row.id, e.target.checked)
                }
              />
              <span className={excluded.has(row.id) ? "text-content-subtle" : ""}>
                {row.label}
              </span>
              {excluded.has(row.id) && (
                <span className="ml-auto text-warn">removed</span>
              )}
            </label>
          ))}
          <div className="text-content-subtle italic pt-0.5">
            Exclude drops the face's plate on recompile (interior end caps are
            unplated — excluding them is a no-op).
          </div>
        </div>
      )}
    </div>
  );
};

// Editable detail for a loft (swept-band) cell: the two bounding stations'
// params (Phase 3a), insert/remove-station, a Move gizmo that translates the
// WHOLE member, and the per-face EXCLUDE list (Phase 3b). A face picked in the
// scene (face select-mode) arrives as selection.faceIndex and highlights its
// row. Openings on loft faces remain deferred (backend geometry pending).
const LoftInfo: React.FC<{
  cell: BuilderCell;
  selection: BuilderSelection;
}> = ({ cell, selection }) => {
  const band = cell.loft;
  const gizmoMode = useCellBuilderStore((s) => s.gizmoMode);
  const setGizmoMode = useCellBuilderStore((s) => s.setGizmoMode);
  const insertLoftStation = useCellBuilderStore((s) => s.insertLoftStation);
  const removeLoftStation = useCellBuilderStore((s) => s.removeLoftStation);
  const loftMembers = useCellBuilderStore((s) => s.loftMembers);
  const setLoftMemberMetadata = useCellBuilderStore((s) => s.setLoftMemberMetadata);
  if (!band) return null;
  const member = band.member;
  const memberDoc = loftMembers.find((m) => m.NAME === member);
  const loIndex = band.bay;
  const hiIndex = band.bay + 1;
  // 2 stations -> a single bay: deleting the hi station would drop below the
  // TopoLoftMember minimum, so disable it.
  const atMin = band.bandCount <= 1;
  return (
    <div className="flex flex-col gap-1.5 px-1 pt-1">
      <div className="text-content-muted">
        Loft band — member <span className="text-content">{member}</span>, bay{" "}
        {band.bay + 1} of {band.bandCount}
      </div>
      <div
        className="flex items-center gap-1"
        title="Move the whole loft member (drag the widget in the scene, or use the grid nudge)"
      >
        <span className="text-content">gizmo</span>
        {(
          [
            ["translate", "Move"],
            ["none", "Off"],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            className={
              "px-1.5 py-0.5 rounded-sm " +
              (gizmoMode === m
                ? "bg-accent text-white"
                : "bg-surface-2 text-content hover:bg-surface-3")
            }
            onClick={() => setGizmoMode(m)}
            aria-pressed={gizmoMode === m}
          >
            {label}
          </button>
        ))}
      </div>
      <EditableStation
        label="Station"
        member={member}
        stationIndex={loIndex}
        station={band.stationLo}
      />
      <EditableStation
        label="→ next"
        member={member}
        stationIndex={hiIndex}
        station={band.stationHi}
      />
      <div className="flex items-center gap-1">
        <button
          className={btn}
          onClick={() => insertLoftStation(member, loIndex)}
          title="Insert a station after this bay's lo station (splits the bay in two)"
        >
          Insert station
        </button>
        <button
          className={btn}
          disabled={atMin}
          onClick={() => removeLoftStation(member, hiIndex)}
          title={
            atMin
              ? "A loft member needs at least 2 stations"
              : "Delete this bay's hi station (merges the adjacent bays)"
          }
        >
          Delete station
        </button>
      </div>
      <LoftFaces
        band={band}
        pickedFaceIndex={
          selection.kind === "face" ? selection.faceIndex : undefined
        }
      />
      {/* Member-level user metadata (shared by all bays of this loft member). */}
      <MetadataFields
        idPrefix={member}
        meta={asMetaObject(memberDoc?.METADATA)}
        onCommit={(next) => setLoftMemberMetadata(member, next ?? {})}
      />
    </div>
  );
};

// User-defined extended metadata editor — free-form key/value rows the compiler
// ignores but the DB round-trips, so a viewer/integration can attach its own
// config to any topology instance. Generic over the backing store: a cell's
// params.METADATA or a loft member's METADATA (``onCommit`` persists the next
// map, or null to clear). All map math lives in the pure `metadata` helpers;
// this component only wires the rows to them.
const MetadataFields: React.FC<{
  meta: MetaMap;
  onCommit: (next: MetaMap | null) => void;
  idPrefix: string;
}> = ({ meta, onCommit, idPrefix }) => {
  const [open, setOpen] = React.useState(false);
  const entries = Object.entries(meta);
  // Commit a next map, but skip a no-op (helpers return the SAME ref when
  // nothing changed) so an identity edit doesn't push an undo step / mark dirty.
  // Empty -> null so the caller drops the key entirely (no empty METADATA={}).
  const push = (next: MetaMap) => {
    if (next !== meta) onCommit(metaOrNull(next));
  };
  const renameKey = (oldKey: string, newKey: string) =>
    push(renameMetadataKey(meta, oldKey, newKey));
  const setValue = (key: string, value: string) =>
    push(setMetadataValue(meta, key, value));
  const remove = (key: string) => push(removeMetadataKey(meta, key));
  const add = () => push(addMetadataKey(meta));
  return (
    <div className="border-t border-edge pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-surface-2 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>▸</span>
        <span className="font-semibold">Metadata</span>
        <span className="text-content-muted">({entries.length})</span>
      </button>
      {open && (
        <div className="flex flex-col gap-1 px-1 pt-1">
          {entries.length === 0 && (
            <div className="text-content-subtle italic">
              No metadata. Add your own fields — kept in the DB, ignored by the compiler.
            </div>
          )}
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-center gap-1">
              <input
                className={`${inputCls} w-24`}
                defaultValue={k}
                key={idPrefix + "|" + k}
                onBlur={(e) => renameKey(k, e.target.value.trim())}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                }}
                title="Field name"
              />
              <input
                className={`${inputCls} flex-1 min-w-0`}
                // Uncontrolled + parse-on-blur: numbers/bools/JSON are only
                // coerced once the field is committed, so typing "5." or "-"
                // isn't clobbered mid-keystroke. `key` includes the formatted
                // value so an external change (undo/redo) reseeds the field.
                defaultValue={formatMetadataValue(v)}
                key={idPrefix + "|v|" + k + "|" + formatMetadataValue(v)}
                onBlur={(e) => setValue(k, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                }}
                title="Value — numbers, true/false and JSON are stored typed; anything else stays text"
              />
              <button
                className="px-1 rounded-sm text-content-muted hover:bg-surface-3 hover:text-white"
                onClick={() => remove(k)}
                title="Remove field"
              >
                ✕
              </button>
            </div>
          ))}
          <button className={btn} onClick={add}>
            + Add field
          </button>
        </div>
      )}
    </div>
  );
};

// "Show as CAD": swap this equipment's placeholder box for its type's linked
// CAD model in the main viewer. Only shown when the resolved equipment type has
// a CAD asset (`has_cad`, surfaced on the type option). The 3D controller lazily
// loads the type's preview GLB, seats it at the cell placement, and hides the
// box while active; toggling off reverts to the box. State lives in the store
// (`cadPreviewCells`) so it survives re-renders and drives the controller.
const ShowAsCadToggle: React.FC<{ cell: BuilderCell }> = ({ cell }) => {
  const equipmentTypes = useCellBuilderStore((s) => s.equipmentTypes);
  const cadPreviewCells = useCellBuilderStore((s) => s.cadPreviewCells);
  const toggleCadPreview = useCellBuilderStore((s) => s.toggleCadPreview);
  // Derived in a memo (never returned fresh from a selector — the unstable-
  // selector infinite-render trap).
  const hasCad = React.useMemo(() => {
    if (!cell.equipmentType) return false;
    const key = cell.equipmentType.toLowerCase();
    const t =
      equipmentTypes.find((o) => o.slug.toLowerCase() === key) ??
      equipmentTypes.find((o) => o.name.toLowerCase() === key);
    return Boolean(t?.has_cad);
  }, [cell.equipmentType, equipmentTypes]);
  if (!hasCad) return null;
  const on = cadPreviewCells.includes(cell.id);
  return (
    <button
      className={
        "self-start px-2 py-1 rounded-sm " +
        (on
          ? "bg-accent text-white"
          : "bg-surface-2 text-content hover:bg-surface-3")
      }
      onClick={() => toggleCadPreview(cell.id)}
      aria-pressed={on}
      title="Render this equipment's linked CAD model in the viewer instead of the placeholder box"
    >
      {on ? "Showing CAD ✓" : "Show as CAD"}
    </button>
  );
};

const SelectionSection: React.FC<{ selection: BuilderSelection }> = ({
  selection,
}) => {
  const cells = useCellBuilderStore((s) => s.cells);
  const setSelection = useCellBuilderStore((s) => s.setSelection);
  const applyFaceExtension = useCellBuilderStore((s) => s.applyFaceExtension);
  const setEdgeLength = useCellBuilderStore((s) => s.setEdgeLength);
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  const renameCell = useCellBuilderStore((s) => s.renameCell);
  const gizmoMode = useCellBuilderStore((s) => s.gizmoMode);
  const setGizmoMode = useCellBuilderStore((s) => s.setGizmoMode);
  const insertOpeningOnFace = useCellBuilderStore((s) => s.insertOpeningOnFace);
  const setCellEnclosed = useCellBuilderStore((s) => s.setCellEnclosed);
  // Cell grouping (per-group blueprints). Each of these is a stable reference
  // (array/string/function that only changes on mutation), so selecting them
  // directly is safe — see the unstable-selector note above.
  const groups = useCellBuilderStore((s) => s.groups);
  const setCellGroup = useCellBuilderStore((s) => s.setCellGroup);
  const engines = useCellBuilderStore((s) => s.engines);
  const selectedEngine = useCellBuilderStore((s) => s.selectedEngine);
  const supportsGrouping = Boolean(
    engines.find((e) => e.slug === selectedEngine)?.supports_grouping,
  );
  // Select the stable blueprintOptions reference and derive the enclosed-cells
  // list in a memo. Returning ``... ?? []`` straight from the selector produced a
  // FRESH array every render when enclosed_cells is absent, which useSyncExternal-
  // Store reads as a changed snapshot → infinite re-render ("Maximum update depth
  // exceeded"), crashing the whole viewer when a cell is selected on a model with
  // no enclosed cells (e.g. an imported workbook).
  const blueprintOptions = useCellBuilderStore((s) => s.blueprintOptions);
  const enclosedCells = React.useMemo(
    () =>
      (blueprintOptions as { enclosed_cells?: string[] }).enclosed_cells ?? [],
    [blueprintOptions],
  );
  const [open, setOpen] = React.useState(true);
  const [propsOpen, setPropsOpen] = React.useState(true);
  const [extendBy, setExtendBy] = React.useState(0.5);
  // Deliberately no re-open on a new pick: clicking another cell must not
  // force-expand a section the user has collapsed. The collapse state persists.

  const cell = cells[selection.cellId];
  if (!cell) return null;

  const side =
    selection.kind === "face" && selection.faceIndex !== undefined
      ? BOX_FACE_SIDES[selection.faceIndex]
      : null;
  const edgeAxis = selection.edge?.axis;
  const title =
    cell.kind === "loft" && cell.loft
      ? `Bay ${cell.loft.bay} of ${cell.loft.member}`
      : selection.kind === "cell"
        ? `Cell ${cell.name}`
        : selection.kind === "face"
          ? `Face ${side?.label ?? "?"} of ${cell.name}`
          : `Edge along ${axisLabel(edgeAxis ?? 0)} of ${cell.name}`;

  return (
    <div className="border-t border-edge pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-surface-2 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold truncate">{title}</span>
        <span
          className="ml-auto px-1 rounded-sm hover:bg-surface-3"
          title="Clear selection (Esc)"
          onClick={(e) => {
            e.stopPropagation();
            setSelection(null);
          }}
        >
          ✕
        </span>
      </button>
      {open && cell.kind === "loft" && (
        <LoftInfo cell={cell} selection={selection} />
      )}
      {open && cell.kind !== "loft" && (
        <div className="flex flex-col gap-1.5 px-1 pt-1">
          <div
            className="flex items-center gap-1"
            title="Direct-manipulation gizmo for this cell (also via long-press / right-click in the scene)"
          >
            <span className="text-content">gizmo</span>
            {
              // Equipment is sized by its type — offer Move + Rotate, no Resize.
              (cell.kind === "cell"
                ? ([
                    ["translate", "Move"],
                    ["resize", "Resize"],
                    ["none", "Off"],
                  ] as const)
                : ([
                    ["translate", "Move"],
                    ["rotate", "Rotate"],
                    ["none", "Off"],
                  ] as const)
              ).map(([m, label]) => (
                <button
                  key={m}
                  className={
                    "px-1.5 py-0.5 rounded-sm " +
                    (gizmoMode === m
                      ? "bg-accent text-white"
                      : "bg-surface-2 text-content hover:bg-surface-3")
                  }
                  onClick={() => setGizmoMode(m)}
                  aria-pressed={gizmoMode === m}
                >
                  {label}
                </button>
              ))
            }
          </div>
          {selection.kind === "face" && side && (
            <>
              {/* Face-extend resizes the box — cells only; equipment is sized
                  by its type. */}
              {cell.kind === "cell" && (
                <div className="flex items-center gap-1">
                  <span className="text-content">Extend by</span>
                  <input
                    type="number"
                    step={0.1}
                    className={`${inputCls} w-20`}
                    value={extendBy}
                    onChange={(e) => setExtendBy(Number(e.target.value))}
                  />
                  <button
                    className={btn}
                    onClick={() =>
                      applyFaceExtension(
                        cell.id,
                        selection.faceIndex!,
                        extendBy,
                      )
                    }
                    title="Extend (negative contracts) this face outward"
                  >
                    Apply
                  </button>
                </div>
              )}
              {cell.kind === "cell" && (
                <label
                  className="flex items-center gap-1"
                  title={`TopoSpace side exclusion SE${side.se} — omit this side when building`}
                >
                  <input
                    type="checkbox"
                    checked={Boolean(cell.params[`SE${side.se}`])}
                    onChange={(e) =>
                      setCellParam(
                        cell.id,
                        `SE${side.se}`,
                        e.target.checked || null,
                      )
                    }
                  />
                  Exclude side (SE{side.se})
                </label>
              )}
              {cell.kind === "cell" && (
                <div className="flex items-center gap-1">
                  <span className="text-content">Insert opening</span>
                  <button
                    className={btn}
                    onClick={() =>
                      insertOpeningOnFace(cell.id, selection.faceIndex!, "door")
                    }
                    title="Add a door opening straddling this face (0.9 × 2.1 m, at the floor)"
                  >
                    + Door
                  </button>
                  <button
                    className={btn}
                    onClick={() =>
                      insertOpeningOnFace(
                        cell.id,
                        selection.faceIndex!,
                        "window",
                      )
                    }
                    title="Add a window opening straddling this face (1.2 × 1.0 m, 1.0 m sill)"
                  >
                    + Window
                  </button>
                </div>
              )}
            </>
          )}
          {selection.kind === "edge" &&
            edgeAxis !== undefined &&
            cell.kind === "cell" && (
              <div className="flex items-center gap-1">
                <span className="text-content">
                  Length {axisLabel(edgeAxis)}
                </span>
                <input
                  type="number"
                  step={0.1}
                  min={0.1}
                  className={`${inputCls} w-20`}
                  value={cell.size[edgeAxis]}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (v > 0) setEdgeLength(cell.id, edgeAxis, v);
                  }}
                />
              </div>
            )}
          {selection.kind === "cell" && cell.kind === "equipment" && (
            <ShowAsCadToggle cell={cell} />
          )}
          {selection.kind === "cell" && (
            <div className="border-t border-edge pt-1">
              <button
                className="flex items-center gap-1 w-full text-left hover:bg-surface-2 rounded-sm px-1"
                onClick={() => setPropsOpen((v) => !v)}
                aria-expanded={propsOpen}
              >
                <span
                  className={"transition-transform " + (propsOpen ? "rotate-90" : "")}
                >
                  ▸
                </span>
                <span className="font-semibold">Properties</span>
              </button>
              {propsOpen && (
                <div className="flex flex-col gap-1 px-1 pt-1">
                  <label className="flex items-center gap-1">
                    <span className="text-content">name</span>
                    <input
                      type="text"
                      className={`${inputCls} flex-1 min-w-0`}
                      defaultValue={cell.name}
                      key={cell.id + cell.name}
                      onBlur={(e) => renameCell(cell.id, e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.currentTarget.blur();
                      }}
                      title={
                        cell.kind === "equipment"
                          ? "Renaming rewrites this equipment's system connections too"
                          : "Cell name"
                      }
                    />
                  </label>
                  <div className="text-content-muted">
                    origin {cell.origin.map((v) => v.toFixed(2)).join(", ")} · size{" "}
                    {cell.size.map((v) => v.toFixed(2)).join(", ")}
                  </div>
                  {cell.kind === "cell" && (
                    <label
                      className="flex items-center gap-1"
                      title="Plate every bounding face of this cell (walls + floor/roof decks, stiffeners facing inward) so the room is fully enclosed"
                    >
                      <input
                        type="checkbox"
                        checked={enclosedCells.includes(cell.name)}
                        onChange={(e) =>
                          setCellEnclosed(cell.name, e.target.checked)
                        }
                      />
                      Enclosed room (plated walls)
                    </label>
                  )}
                  {cell.kind === "cell" && supportsGrouping && (
                    <label
                      className="flex items-center gap-1"
                      title="Assign this cell to a group; each group compiles with its own blueprint. Manage groups in the Build tab."
                    >
                      <span className="text-content">group</span>
                      <select
                        className="flex-1 min-w-0 text-content bg-surface-2 border border-edge rounded-sm px-1 py-0.5"
                        value={cell.group ?? ""}
                        onChange={(e) =>
                          setCellGroup(cell.id, e.target.value || null)
                        }
                      >
                        <option value="">— none —</option>
                        {groups.map((g) => (
                          <option key={g.name} value={g.name}>
                            {g.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {(cell.kind === "cell" ? SPACE_PARAMS : EQUIPMENT_PARAMS).map(
                    (f) => (
                      <ParamRow key={f.key} cell={cell} field={f} />
                    ),
                  )}
                </div>
              )}
            </div>
          )}
          {cell.kind === "equipment" && (
            <EquipmentSystems
              equipmentName={cell.name}
              equipmentType={cell.equipmentType}
            />
          )}
          {/* Per-instance user metadata — shown for a whole-cell pick of any box
              instance (cell / equipment / opening). */}
          {selection.kind === "cell" && (
            <MetadataFields
              idPrefix={cell.id}
              meta={asMetaObject(cell.params.METADATA)}
              onCommit={(next) => setCellParam(cell.id, "METADATA", next)}
            />
          )}
        </div>
      )}
    </div>
  );
};

// Host: renders the selection detail inside the Selected Object Info panel when
// a procedural model has an active selection; otherwise nothing.
const CellBuilderSelectionInfo: React.FC = () => {
  const active = useCellBuilderStore((s) => s.active);
  const selection = useCellBuilderStore((s) => s.selection);
  if (!active || !selection) return null;
  return (
    <div className="mt-3 border-t border-edge pt-2 text-xs text-white">
      <div className="font-bold mb-1">Procedural cell</div>
      <SelectionSection selection={selection} />
    </div>
  );
};

export default CellBuilderSelectionInfo;
