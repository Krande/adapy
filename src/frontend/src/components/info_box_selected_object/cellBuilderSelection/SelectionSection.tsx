// One picked cell -- a space (or a face or an edge of it), an equipment, an opening or a loft
// bay -- as a PropertyItemLayout: where it is (its space and structure, as links), its badge and
// name, its box, and its groups of fields. No gizmo buttons, include toggles or titles of its
// own: the Model panel and the toolbar have those.

import React from "react";
import { useCellBuilderStore, type BuilderCell, type BuilderSelection } from "@/state/cellBuilderStore";
import { OPENING_SIDES } from "@/state/cellbuilder/docCodec";
import { BOX_FACE_SIDES } from "@/utils/cellbuilder/snap";
import { equipmentCadFitted, resizeRefusal } from "@/utils/cellbuilder/cellResize";
import { asMetaObject } from "@/utils/cellbuilder/metadata";
import { LoftInfo } from "./LoftInfo";
import { ParamCheck, ParamNumber, ParamNumbersInline, ParamSelect, ParamText, btn } from "./fields";
import { EquipmentSystems } from "./EquipmentSystems";
import { MetadataFields } from "./MetadataFields";
import { Field, FieldCheck, PropertyItemLayout, propertyInputCls, type PropertyItemGroup, type PropertyItemParents } from "./PropertyItemLayout";

// "Show as CAD": swap this equipment's placeholder box for its type's linked
// CAD model in the main viewer. Only shown when the resolved equipment type has
// a CAD asset (`has_cad`, surfaced on the type option). The 3D controller lazily
// loads the type's preview GLB, seats it at the cell placement, and hides the
// box while active; toggling off reverts to the box. State lives in the store
// (`cadPreviewCells`) so it survives re-renders and drives the controller.
function useHasCad(cell: BuilderCell): boolean {
  const equipmentTypes = useCellBuilderStore((s) => s.equipmentTypes);
  // Derived in a memo (never returned fresh from a selector — the unstable-
  // selector infinite-render trap).
  return React.useMemo(() => {
    if (!cell.equipmentType) return false;
    const key = cell.equipmentType.toLowerCase();
    const t =
      equipmentTypes.find((o) => o.slug.toLowerCase() === key) ??
      equipmentTypes.find((o) => o.name.toLowerCase() === key);
    return Boolean(t?.has_cad);
  }, [cell.equipmentType, equipmentTypes]);
}

const ShowAsCadToggle: React.FC<{ cell: BuilderCell }> = ({ cell }) => {
  const cadPreviewCells = useCellBuilderStore((s) => s.cadPreviewCells);
  const toggleCadPreview = useCellBuilderStore((s) => s.toggleCadPreview);
  const on = cadPreviewCells.includes(cell.id);
  return (
    <button
      className={"self-start px-2 py-0.5 rounded-sm " + (on ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300 hover:bg-gray-600")}
      onClick={() => toggleCadPreview(cell.id)}
      aria-pressed={on}
      title="Render this equipment's linked CAD model in the viewer instead of the placeholder box"
    >
      {on ? "Showing CAD ✓" : "Show as CAD"}
    </button>
  );
};

const cap = (s: string) => s[0].toUpperCase() + s.slice(1);

/** The badge of a cell: what kind of item it is. */
export function cellBadge(cell: BuilderCell): string {
  if (cell.kind === "cell") return "Space";
  if (cell.kind === "equipment") return cell.equipmentType ? cap(cell.equipmentType) : "Equipment";
  if (cell.kind === "opening") return cell.subtype ? cap(cell.subtype) : "Opening";
  return "Loft";
}

/** The parents line of a pick: its space (not for a space itself) and its structure, each selecting it. */
export function useItemParents(place: { structure?: string; space?: string }, selfId?: string): PropertyItemParents {
  const cells = useCellBuilderStore((s) => s.cells);
  const groups = useCellBuilderStore((s) => s.groups);
  const setSelection = useCellBuilderStore((s) => s.setSelection);
  const setActiveGroup = useCellBuilderStore((s) => s.setActiveGroup);
  const space = place.space ? Object.values(cells).find((c) => c.kind === "cell" && c.name === place.space) : undefined;
  const group = place.structure ? groups.find((g) => g.name === place.structure) : undefined;
  return {
    ...(place.space && space?.id !== selfId
      ? { space: { name: place.space, ...(space ? { onSelect: () => setSelection({ kind: "cell", cellId: space.id }) } : {}) } }
      : {}),
    ...(place.structure
      ? {
          structure: {
            name: place.structure,
            ...(group?.blueprint ? { blueprint: group.blueprint } : {}),
            // A structure is picked as the active one, with nothing else selected: its own view.
            ...(group ? { onSelect: () => (setSelection(null), setActiveGroup(group.name)) } : {}),
          },
        }
      : {}),
  };
}

/** A space's groups: its grid and beams, enclosure, sides, and where it is filed. */
function useSpaceGroups(cell: BuilderCell, selection: BuilderSelection): PropertyItemGroup[] {
  const setCellEnclosed = useCellBuilderStore((s) => s.setCellEnclosed);
  const applyFaceExtension = useCellBuilderStore((s) => s.applyFaceExtension);
  const insertOpeningOnFace = useCellBuilderStore((s) => s.insertOpeningOnFace);
  const groups = useCellBuilderStore((s) => s.groups);
  const setCellGroup = useCellBuilderStore((s) => s.setCellGroup);
  const engines = useCellBuilderStore((s) => s.engines);
  const selectedEngine = useCellBuilderStore((s) => s.selectedEngine);
  const supportsGrouping = Boolean(engines.find((e) => e.slug === selectedEngine)?.supports_grouping);
  // Select the stable blueprintOptions reference and derive the enclosed-cells
  // list in a memo. Returning ``... ?? []`` straight from the selector produced a
  // FRESH array every render when enclosed_cells is absent, which useSyncExternal-
  // Store reads as a changed snapshot → infinite re-render ("Maximum update depth
  // exceeded"), crashing the whole viewer when a cell is selected on a model with
  // no enclosed cells (e.g. an imported workbook).
  const blueprintOptions = useCellBuilderStore((s) => s.blueprintOptions);
  const enclosedCells = React.useMemo(() => (blueprintOptions as { enclosed_cells?: string[] }).enclosed_cells ?? [], [blueprintOptions]);
  const [extendBy, setExtendBy] = React.useState(0.5);
  const side = selection.kind === "face" && selection.faceIndex !== undefined ? BOX_FACE_SIDES[selection.faceIndex] : null;

  const out: PropertyItemGroup[] = [
    {
      title: "Grid",
      fields: (
        <Field label="Create" inline help={{ list: "spaces", column: "GRID_X_CREATE" }}>
          <ParamCheck cell={cell} column="GRID_X_CREATE" label="X" />
          <ParamCheck cell={cell} column="GRID_Y_CREATE" label="Y" />
          <ParamCheck cell={cell} column="GRID_Z_CREATE" label="Z" />
        </Field>
      ),
    },
    {
      title: "Beams",
      fields: (
        <>
          <Field label="Switch" inline help={{ list: "spaces", column: "SWITCH_BM_DIR_VERTICAL" }}>
            <ParamCheck cell={cell} column="SWITCH_BM_DIR_VERTICAL" label="vert." />
            <ParamCheck cell={cell} column="SWITCH_BM_DIR_HORIZONTAL" label="horiz." />
          </Field>
          <Field label="Floor" inline help={{ list: "spaces", column: "FLIP_FLOOR" }}>
            <ParamCheck cell={cell} column="FLIP_FLOOR" label="flip" />
          </Field>
        </>
      ),
    },
    {
      title: "Enclosed",
      fields: (
        <>
          <Field label="Room" help="Plate every bounding face of this space (walls and floor / roof decks, stiffeners facing inward) so the room is fully enclosed">
            <FieldCheck label="plated walls" checked={enclosedCells.includes(cell.name)} onChange={(v) => setCellEnclosed(cell.name, v)} />
          </Field>
          {/* The six sides on one row; the picked face's side is the one marked. */}
          <Field label="Exclude" inline help={{ list: "spaces", column: "SE0" }}>
            {[0, 1, 2, 3, 4, 5].map((se) => (
              <span key={se} className={side?.se === se ? "rounded-sm bg-blue-900/60 px-0.5" : ""}>
                <ParamCheck cell={cell} column={`SE${se}`} label={`SE${se}`} />
              </span>
            ))}
          </Field>
        </>
      ),
    },
    {
      title: "General",
      fields: (
        <>
          {supportsGrouping && (
            <Field label="Group" help={{ list: "spaces", column: "STRUCTURE_NAME" }}>
              <select
                className={`${propertyInputCls} w-full`}
                aria-label="Group (STRUCTURE_NAME)"
                value={cell.group ?? ""}
                onChange={(e) => setCellGroup(cell.id, e.target.value || null)}
              >
                <option value="">— none —</option>
                {groups.map((g) => (
                  <option key={g.name} value={g.name}>
                    {g.name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <ParamText cell={cell} column="AREA" label="Area" />
          <ParamNumber cell={cell} column="PRIORITY" label="Priority" step={1} />
        </>
      ),
    },
  ];
  // A face picked: what can be done from it.
  if (side && selection.kind === "face") {
    out.unshift({
      title: `Face ${side.label}`,
      fields: (
        <>
          <Field label="Extend by" help="Extend (negative contracts) this face outward">
            <input type="number" step={0.1} className={`${propertyInputCls} w-20`} value={extendBy} aria-label="Extend by" onChange={(e) => setExtendBy(Number(e.target.value))} />
            <button className={btn} onClick={() => applyFaceExtension(cell.id, selection.faceIndex!, extendBy)}>
              Apply
            </button>
          </Field>
          <Field label="Opening" inline>
            <button
              className={btn}
              onClick={() => insertOpeningOnFace(cell.id, selection.faceIndex!, "door")}
              title="Add a door opening straddling this face (0.9 × 2.1 m, at the floor)"
            >
              + Door
            </button>
            <button
              className={btn}
              onClick={() => insertOpeningOnFace(cell.id, selection.faceIndex!, "window")}
              title="Add a window opening straddling this face (1.2 × 1.0 m, 1.0 m sill)"
            >
              + Window
            </button>
          </Field>
        </>
      ),
    });
  }
  return out;
}

/** An equipment's groups: placement, mass and centre of gravity, systems, CAD. */
function useEquipmentGroups(cell: BuilderCell): PropertyItemGroup[] {
  const setCellRotation = useCellBuilderStore((s) => s.setCellRotation);
  const hasCad = useHasCad(cell);
  const rotation = cell.rotation ?? [0, 0, 0];
  const out: PropertyItemGroup[] = [
    {
      title: "Placement",
      fields: (
        <>
          <ParamSelect cell={cell} column="SPACE_LOC" label="Location" options={["FLOOR", "ROOF"]} />
          <Field label="Rotation°" inline help={{ list: "equipments", column: "ROT_Z" }}>
            {(["X", "Y", "Z"] as const).map((a, i) => (
              <label key={a} className="inline-flex min-w-0 flex-1 items-center gap-0.5">
                <span className="text-gray-500">{a}</span>
                <input
                  type="number"
                  step={15}
                  className={`${propertyInputCls} w-full`}
                  aria-label={`Rotation ${a} (ROT_${a})`}
                  value={rotation[i]}
                  onChange={(e) => {
                    const n = Number(e.target.value);
                    if (!Number.isFinite(n)) return;
                    const next = [...rotation] as [number, number, number];
                    next[i] = n;
                    setCellRotation(cell.id, next);
                  }}
                />
              </label>
            ))}
          </Field>
        </>
      ),
    },
    {
      title: "Mass & COG",
      fields: (
        <>
          <ParamNumber cell={cell} column="massDry" label="Dry [kg]" step={100} />
          <ParamNumber cell={cell} column="massCont" label="Content [kg]" step={100} />
          <ParamNumbersInline
            cell={cell}
            label="COG"
            columns={[
              { column: "COGx", label: "x" },
              { column: "COGy", label: "y" },
              { column: "COGz", label: "z" },
            ]}
          />
        </>
      ),
    },
    { title: "Systems", fields: <EquipmentSystems equipmentName={cell.name} equipmentType={cell.equipmentType} /> },
  ];
  if (hasCad) out.push({ title: "CAD", fields: <ShowAsCadToggle cell={cell} /> });
  return out;
}

/** An opening's frame: its subtype, and the side of its space it is placed on. */
function useOpeningGroups(cell: BuilderCell): PropertyItemGroup[] {
  const updateCell = useCellBuilderStore((s) => s.updateCell);
  return [
    {
      title: "Frame",
      fields: (
        <>
          <Field label="Subtype" help={{ list: "openings", column: "SUBTYPE" }}>
            <select
              className={`${propertyInputCls} w-full`}
              aria-label="Subtype (SUBTYPE)"
              value={cell.subtype ?? "door"}
              onChange={(e) => updateCell(cell.id, { subtype: e.target.value as BuilderCell["subtype"] })}
            >
              {["door", "window", "opening"].map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </Field>
          {cell.openingFrame === "local" ? (
            // Placed on a side of its space: written back in that local form (POS / SIZE / DEPTH).
            <ParamSelect cell={cell} column="SPACE_SIDE" label="Side" options={OPENING_SIDES} />
          ) : (
            <Field label="Frame" help="A box in the model's own coordinates (X / Y / Z, DX / DY / DZ)">
              <span className="text-gray-400">global box</span>
            </Field>
          )}
        </>
      ),
    },
  ];
}

const NO_GROUPS: PropertyItemGroup[] = [];

/** The picked cell as a PropertyItemLayout. */
export const SelectionSection: React.FC<{ selection: BuilderSelection; place: { structure?: string; space?: string } }> = ({ selection, place }) => {
  const cells = useCellBuilderStore((s) => s.cells);
  const renameCell = useCellBuilderStore((s) => s.renameCell);
  const updateCell = useCellBuilderStore((s) => s.updateCell);
  const setCellParam = useCellBuilderStore((s) => s.setCellParam);
  // Whether an equipment's box follows its CAD model (no resize then).
  const equipmentCad = useCellBuilderStore((s) => s.equipmentCad);
  const equipmentTypes = useCellBuilderStore((s) => s.equipmentTypes);
  const cell = selection.kind === "ref" ? undefined : cells[selection.cellId];
  const parents = useItemParents(place, cell?.id);
  // Every kind's hooks run on every render (the rules of hooks); only the cell's kind is shown.
  const fallback = cell ?? ({ id: "", name: "", kind: "cell", origin: [0, 0, 0], size: [1, 1, 1], params: {} } as BuilderCell);
  const spaceGroups = useSpaceGroups(fallback, selection);
  const equipmentGroups = useEquipmentGroups(fallback);
  const openingGroups = useOpeningGroups(fallback);
  if (!cell) return null;

  const metadata: PropertyItemGroup[] =
    selection.kind === "cell" && cell.kind !== "loft"
      ? [
          {
            title: "Metadata",
            fields: <MetadataFields idPrefix={cell.id} meta={asMetaObject(cell.params.METADATA)} onCommit={(next) => setCellParam(cell.id, "METADATA", next)} />,
          },
        ]
      : NO_GROUPS;
  if (cell.kind === "loft") {
    return (
      <PropertyItemLayout
        badge="Loft"
        name={cell.loft?.member ?? cell.name}
        parents={parents}
        groups={[{ title: `Bay ${(cell.loft?.bay ?? 0) + 1} of ${cell.loft?.bandCount ?? 1}`, fields: <LoftInfo cell={cell} selection={selection} /> }]}
        dataAttrs={{ "data-builder-item": cell.kind }}
      />
    );
  }
  const own = cell.kind === "cell" ? spaceGroups : cell.kind === "equipment" ? equipmentGroups : openingGroups;
  const canResize = resizeRefusal(cell.kind, { cadFitted: equipmentCadFitted(cell, { equipmentCad, equipmentTypes }) }) === null;
  return (
    <PropertyItemLayout
      badge={cellBadge(cell)}
      name={cell.name}
      onRename={(name) => renameCell(cell.id, name)}
      parents={parents}
      geometry={{
        origin: cell.origin,
        size: cell.size,
        onChange: ({ origin, size }) => updateCell(cell.id, { origin: [...origin], size: [...size] }),
        editable: { size: canResize },
      }}
      groups={[...own, ...metadata]}
      dataAttrs={{ "data-builder-item": cell.kind }}
    />
  );
};
