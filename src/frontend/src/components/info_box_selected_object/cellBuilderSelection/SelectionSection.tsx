// One picked cell -- a space (or a face or an edge of it), an equipment, an opening or a loft
// bay -- as a PropertyItemLayout: where it is (its space and structure, as links), its badge and
// name, its box, and its groups of fields. No gizmo buttons, include toggles or titles of its
// own: the Model panel and the toolbar have those.

import React from "react";
import { useCellBuilderStore, type BuilderCell, type BuilderSelection } from "@/state/cellBuilderStore";
import { OPENING_SIDES } from "@/state/cellbuilder/docCodec";
import { equipmentCadFitted, resizeRefusal } from "@/utils/cellbuilder/cellResize";
import { asMetaObject } from "@/utils/cellbuilder/metadata";
import { LoftInfo } from "./LoftInfo";
import { ParamNumber, ParamSelect, ParamText } from "./fields";
import { EquipmentSystems } from "./EquipmentSystems";
import { MetadataFields } from "./MetadataFields";
import { Field, PropertyItemLayout, propertyInputCls, type PropertyItemGroup, type PropertyItemParents } from "./PropertyItemLayout";
import { useSpaceGroups } from "./spaceGroups";

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

/** An equipment's groups: placement (location, reinforcement section), mass, systems, CAD.
 *  Rotation and COG moved into the shared Geometry grid below: one X/Y/Z block, not a
 *  separate inline row + style each. */
function useEquipmentGroups(cell: BuilderCell): PropertyItemGroup[] {
  const setEquipmentLocation = useCellBuilderStore((s) => s.setEquipmentLocation);
  const hasCad = useHasCad(cell);
  const out: PropertyItemGroup[] = [
    {
      title: "Placement",
      fields: (
        <>
          <Field label="Location" help={{ list: "equipments", column: "SPACE_LOC" }}>
            <select
              className={`${propertyInputCls} w-full`}
              aria-label="Location (SPACE_LOC)"
              value={(cell.params.SPACE_LOC as string) ?? "FLOOR"}
              onChange={(e) => setEquipmentLocation(cell.id, e.target.value as "FLOOR" | "ROOF")}
            >
              {["FLOOR", "ROOF"].map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </Field>
          {/* The equipment's REINFORCEMENT_SECTION (the section that reinforces the structure it
             is seated in), as a plain text field, round-tripping through params like any other
             engine column (fields.tsx's ParamText), with the engine's own column help. */}
          <ParamText cell={cell} column="REINFORCEMENT_SECTION" label="Reinforcement section" />
        </>
      ),
    },
    {
      title: "Mass",
      fields: (
        <>
          <ParamNumber cell={cell} column="massDry" label="Dry [kg]" step={100} />
          <ParamNumber cell={cell} column="massCont" label="Content [kg]" step={100} />
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
  const setCellRotation = useCellBuilderStore((s) => s.setCellRotation);
  const beginTransaction = useCellBuilderStore((s) => s.beginTransaction);
  const endTransaction = useCellBuilderStore((s) => s.endTransaction);
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
  // One X/Y/Z block for everything a box carries -- Position, Size, and (equipment only)
  // Rotation° and COG as rows in the SAME grid, instead of their own inline style each.
  const isEquipment = cell.kind === "equipment";
  return (
    <PropertyItemLayout
      badge={cellBadge(cell)}
      name={cell.name}
      onRename={(name) => renameCell(cell.id, name)}
      parents={parents}
      geometry={{
        origin: cell.origin,
        size: cell.size,
        ...(isEquipment ? { rotation: cell.rotation ?? [0, 0, 0] } : {}),
        onChange: ({ origin, size, rotation }) => {
          // Rotation and origin/size go through different store actions (setCellRotation vs
          // updateCell); GeometryGrid's write() always hands back the whole set unchanged except
          // the one edited field, so both run on every call -- harmless (setCellRotation already
          // no-ops when its value is unchanged) -- wrapped in one transaction so editing either
          // is still one undo step.
          beginTransaction();
          updateCell(cell.id, { origin: [...origin], size: [...size] });
          if (isEquipment && rotation) setCellRotation(cell.id, [...rotation] as [number, number, number]);
          endTransaction();
        },
        editable: { size: canResize },
        ...(isEquipment
          ? {
              extraRows: [
                {
                  key: "cog",
                  label: "COG",
                  value: [Number(cell.params.COGx) || 0, Number(cell.params.COGy) || 0, Number(cell.params.COGz) || 0],
                  onChange: ([x, y, z]) => {
                    beginTransaction();
                    setCellParam(cell.id, "COGx", x);
                    setCellParam(cell.id, "COGy", y);
                    setCellParam(cell.id, "COGz", z);
                    endTransaction();
                  },
                },
              ],
            }
          : {}),
      }}
      groups={[...own, ...metadata]}
      dataAttrs={{ "data-builder-item": cell.kind }}
    />
  );
};
