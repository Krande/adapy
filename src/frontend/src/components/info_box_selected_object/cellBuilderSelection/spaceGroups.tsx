// A space's property groups (Grid, Beams, Enclosed, General, and a picked face's own group),
// split out of SelectionSection.tsx: the one part of that file with its own sizeable local state
// (the side-exclude checkboxes and their named-view labels, the face-extend input) and its own
// derived lookups (the area-suggestion list, whether the engine is the built-in one).

import React from "react";
import { useCellBuilderStore, type BuilderCell, type BuilderSelection } from "@/state/cellBuilderStore";
import { BOX_FACE_SIDES, nextSpaceExcludeParams, spaceSideExcluded } from "@/utils/cellbuilder/snap";
import { ParamCheck, ParamCombo, ParamNumber, ParamSelect, ParamText, btn } from "./fields";
import { Field, FieldCheck, propertyInputCls, type PropertyItemGroup } from "./PropertyItemLayout";

// The six side-exclude checkboxes, named and ordered by the viewer's own named views (Alt+1..6,
// utils/camera/namedViewDirection.ts) rather than the raw axis signs: each view's label is the
// face seen looking from it (Top -> +Z, Bottom -> -Z, Front -> -Y, Back -> +Y, Left -> -X,
// Right -> +X). `view` is the short name alone, for the aria-label; `label` is what is shown next
// to the checkbox.
const SPACE_EXCLUDE_SIDES: readonly { se: number; view: string; label: string }[] = [
  { se: 1, view: "Top", label: "Top (Z)" },
  { se: 0, view: "Bottom", label: "Bottom (-Z)" },
  { se: 2, view: "Front", label: "Front (-Y)" },
  { se: 3, view: "Back", label: "Back (Y)" },
  { se: 4, view: "Left", label: "Left (-X)" },
  { se: 5, view: "Right", label: "Right (X)" },
];

// spaceSideExcluded / nextSpaceExcludeParams moved to utils/cellbuilder/snap.ts (pure, no React)
// so overlays.ts (hiding an excluded face in 3D) can read the same rule without importing a
// component file.

/** One side-exclude checkbox; see `nextSpaceExcludeParams` for what toggling it writes. */
const SpaceExcludeCheck: React.FC<{ cell: BuilderCell; se: number; view: string; label: string }> = ({ cell, se, view, label }) => {
  const updateCell = useCellBuilderStore((s) => s.updateCell);
  const checked = spaceSideExcluded(cell, se);
  const column = `SE${se}`;
  const onChange = (v: boolean) => updateCell(cell.id, { params: nextSpaceExcludeParams(cell.params, se, v) });
  return <FieldCheck label={label} ariaLabel={`Exclude ${view} (${column})`} help={{ list: "spaces", column }} checked={checked} onChange={onChange} />;
};

/** A space's groups: its grid and beams, enclosure, sides, and where it is filed. */
export function useSpaceGroups(cell: BuilderCell, selection: BuilderSelection): PropertyItemGroup[] {
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
  // "Room / plated walls" toggles blueprintOptions.enclosed_cells, the built-in engine's own
  // blueprint option: a non-built-in engine never reads it, so the toggle is pointless -- and
  // confusing -- on a model built by one.
  const isBuiltinEngine = (selectedEngine || "adapy-default") === "adapy-default";
  // Other spaces' AREA values as datalist suggestions for this one -- distinct, non-empty,
  // sorted, excluding whatever is already in this space's own AREA.
  const cells = useCellBuilderStore((s) => s.cells);
  const areaSuggestions = React.useMemo(() => {
    const current = typeof cell.params?.AREA === "string" ? cell.params.AREA : "";
    const out = new Set<string>();
    for (const c of Object.values(cells)) {
      if (c.kind !== "cell" || c.id === cell.id) continue;
      const v = c.params?.AREA;
      if (typeof v === "string" && v.trim()) out.add(v);
    }
    out.delete(current);
    return [...out].sort();
  }, [cells, cell.id, cell.params?.AREA]);

  const out: PropertyItemGroup[] = [
    {
      title: "Grid",
      fields: (
        // defaultOn: GRID_*_CREATE defaults to True in the model, so unchecking must write an
        // explicit false (ParamCheck's defaultOn) or the model default brings it back checked.
        <Field label="Create" inline help={{ list: "spaces", column: "GRID_X_CREATE" }}>
          <ParamCheck cell={cell} column="GRID_X_CREATE" label="X" defaultOn />
          <ParamCheck cell={cell} column="GRID_Y_CREATE" label="Y" defaultOn />
          <ParamCheck cell={cell} column="GRID_Z_CREATE" label="Z" defaultOn />
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
          {isBuiltinEngine && (
            <Field label="Room" help="Plate every bounding face of this space (walls and floor / roof decks, stiffeners facing inward) so the room is fully enclosed">
              <FieldCheck label="plated walls" checked={enclosedCells.includes(cell.name)} onChange={(v) => setCellEnclosed(cell.name, v)} />
            </Field>
          )}
          {/* The six named views (Top/Bottom/Front/Back/Left/Right), wrapping onto a second row
              if the panel is too narrow for one; the picked face's side is marked. */}
          <Field label="Exclude side" inline>
            {SPACE_EXCLUDE_SIDES.map(({ se, view, label }) => (
              <span key={se} className={side?.se === se ? "rounded-sm bg-blue-900/60 px-0.5" : ""}>
                <SpaceExcludeCheck cell={cell} se={se} view={view} label={label} />
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
          <ParamSelect cell={cell} column="FUNCTION" label="Function" options={["space", "cantilevered_deck"]} />
          <ParamCombo cell={cell} column="AREA" label="Area" suggestions={areaSuggestions} />
          <ParamSelect cell={cell} column="GIRDER_AS_PLATE" label="Girder as plate" options={["x", "y", "z", "false"]} />
          <ParamText cell={cell} column="DESCRIPTION" label="Description" />
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
