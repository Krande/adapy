// One selected cell or equipment item: its gizmo toggles and the editors for it, each
// collapsible.

import React from "react";
import { useCellBuilderStore, type BuilderCell, type BuilderSelection } from "@/state/cellBuilderStore";
import { axisLabel, BOX_FACE_SIDES } from "@/utils/cellbuilder/snap";
import { asMetaObject } from "@/utils/cellbuilder/metadata";
import { LoftInfo } from "./LoftInfo";
import { EQUIPMENT_PARAMS, ParamRow, SPACE_PARAMS, btn, inputCls } from "./fields";
import { EquipmentSystems } from "./EquipmentSystems";
import { MetadataFields } from "./MetadataFields";

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
          ? "bg-blue-600 text-white"
          : "bg-gray-700 text-gray-300 hover:bg-gray-600")
      }
      onClick={() => toggleCadPreview(cell.id)}
      aria-pressed={on}
      title="Render this equipment's linked CAD model in the viewer instead of the placeholder box"
    >
      {on ? "Showing CAD ✓" : "Show as CAD"}
    </button>
  );
};

export const SelectionSection: React.FC<{ selection: BuilderSelection }> = ({
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
    <div className="border-t border-gray-600/60 pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold truncate">{title}</span>
        <span
          className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
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
            <span className="text-gray-300">gizmo</span>
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
                      ? "bg-blue-600 text-white"
                      : "bg-gray-700 text-gray-300 hover:bg-gray-600")
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
                  <span className="text-gray-300">Extend by</span>
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
                  <span className="text-gray-300">Insert opening</span>
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
                <span className="text-gray-300">
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
            <div className="border-t border-gray-600/60 pt-1">
              <button
                className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
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
                    <span className="text-gray-300">name</span>
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
                  <div className="text-gray-400">
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
                      <span className="text-gray-300">group</span>
                      <select
                        className="flex-1 min-w-0 text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5"
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
