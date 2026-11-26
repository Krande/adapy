// The builder's sections of the Selected Object panel, each its own property provider
// (ObjectInfoBoxComponent's CORE_PROPERTY_PROVIDERS), so a plugin's section slots between them
// by order:
//   70 the picked item, as a PropertyItemLayout: its space and structure first (the parents
//      line, each a link that selects it), then its badge and name, its box and its fields
//      (SelectionSection); a plugin's ref gets a minimal one (its kind, name, box where it may
//      move or resize) unless its provider draws its own (`BuilderRefEdit.form`);
//   74 a structure on its own -- picked, or the active one with nothing else picked: its name,
//      position (its origin: everything in it is placed relative to it), blueprint, include
//      and colour.

import React from "react";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import type { BuilderRef } from "@/state/cellbuilder/refsSlice";
import { selectedRef, selectionPlace } from "@/utils/cellbuilder/boxTarget";
import { isGroupIncluded, type CellGroup } from "@/utils/cellbuilder/groups";
import { GroupBlueprintSelect, GroupColourInput, renameStructure } from "@/components/viewer/cellbuilder/panel/GroupsSection";
import { SelectionSection, useItemParents } from "./SelectionSection";
import { Field, FieldCheck, PropertyItemLayout, propertyInputCls } from "./PropertyItemLayout";

/** The structure and the space of the builder's pick (`selectionPlace`); with nothing picked,
 *  the active structure. */
function useBuilderPlace(): { structure?: string; space?: string } {
  const selection = useCellBuilderStore((s) => s.selection);
  const cells = useCellBuilderStore((s) => s.cells);
  const refs = useCellBuilderStore((s) => s.refs);
  const groups = useCellBuilderStore((s) => s.groups);
  const loftMembers = useCellBuilderStore((s) => s.loftMembers);
  const activeGroup = useCellBuilderStore((s) => s.activeGroup);
  return React.useMemo(() => {
    if (selection) return selectionPlace(selection, { cells, refs, groups, loftMembers });
    return activeGroup && groups.some((g) => g.name === activeGroup) ? { structure: activeGroup } : {};
  }, [selection, cells, refs, groups, loftMembers, activeGroup]);
}

/** A plugin's ref, as core knows it: its kind, name and box, editable as its plugin allows. The
 *  plugin adds its own fields in a section of its own (matched on `refOwner`), or draws the whole
 *  form there (`edit.form`). */
const RefItem: React.FC<{ ref_: BuilderRef; place: { structure?: string; space?: string } }> = ({ ref_: r, place }) => {
  const renameRef = useCellBuilderStore((s) => s.renameRef);
  const writeRef = useCellBuilderStore((s) => s.writeRef);
  const parents = useItemParents(place);
  const editable = r.edit.move || r.edit.resize;
  return (
    <PropertyItemLayout
      badge={r.kindLabel || "Item"}
      name={r.name}
      onRename={r.edit.rename ? (name) => renameRef(r.id, name) : undefined}
      parents={parents}
      geometry={{
        origin: r.origin,
        size: r.size,
        ...(editable
          ? {
              onChange: ({ origin, size }) => writeRef(r.id, { origin: [...origin], size: [...size] }),
              editable: { origin: r.edit.move, size: r.edit.resize },
            }
          : {}),
      }}
      dataAttrs={{ "data-builder-ref": r.id }}
    />
  );
};

/** Order 70: the picked cell or ref, with where it is. */
export const BuilderItemSection: React.FC = () => {
  const active = useCellBuilderStore((s) => s.active);
  const selection = useCellBuilderStore((s) => s.selection);
  const refs = useCellBuilderStore((s) => s.refs);
  const place = useBuilderPlace();
  if (!active || !selection) return null;
  const ref = selectedRef(selection, refs);
  if (selection.kind === "ref" && !ref) return null;
  // The provider draws this ref's form itself.
  if (ref?.edit.form) return null;
  return (
    <div className="mt-2 border-t border-gray-500/60" data-builder-section="item">
      {ref ? <RefItem ref_={ref} place={place} /> : <SelectionSection selection={selection} place={place} />}
    </div>
  );
};

/** A structure's position (`CellGroup.origin`), X / Y / Z. Moving it moves everything in the
 *  structure: their coordinates are relative to it. */
const StructurePosition: React.FC<{ group: CellGroup }> = ({ group }) => {
  const setGroupOrigin = useCellBuilderStore((s) => s.setGroupOrigin);
  const origin = group.origin ?? [0, 0, 0];
  return (
    <Field label="Position" inline help="Where the structure sits. Its spaces, equipment and openings are placed relative to it, so changing it moves them all.">
      {origin.map((v, i) => (
        <input
          key={i}
          type="number"
          step={0.1}
          className={`${propertyInputCls} w-16`}
          value={Number(v.toFixed(3))}
          aria-label={`Structure ${group.name} position ${"XYZ"[i]}`}
          onChange={(e) => {
            const n = Number(e.target.value);
            if (e.target.value === "" || !Number.isFinite(n)) return;
            const next: [number, number, number] = [origin[0], origin[1], origin[2]];
            next[i] = n;
            setGroupOrigin(group.name, next);
          }}
        />
      ))}
    </Field>
  );
};

/** Order 74: a structure on its own -- the active one, with nothing picked. With an item picked
 *  its structure is in the item's parents line instead. */
export const BuilderStructureSection: React.FC = () => {
  const active = useCellBuilderStore((s) => s.active);
  const selection = useCellBuilderStore((s) => s.selection);
  const groups = useCellBuilderStore((s) => s.groups);
  const setGroupIncluded = useCellBuilderStore((s) => s.setGroupIncluded);
  const { structure: name } = useBuilderPlace();
  const group = name ? groups.find((g) => g.name === name) : undefined;
  if (!active || !group || selection) return null;
  const included = isGroupIncluded(group);
  return (
    <div className="mt-2 border-t border-gray-500/60" data-builder-section="structure">
      <PropertyItemLayout
        badge="Structure"
        name={group.name}
        onRename={(v) => renameStructure(group.name, v)}
        groups={[
          { title: "Placement", fields: <StructurePosition group={group} /> },
          {
            title: "Build",
            fields: (
              <>
                <Field label="Blueprint" help={{ list: "structures", column: "BLUEPRINT" }}>
                  <GroupBlueprintSelect group={group} />
                </Field>
                <Field label="Include" help={{ list: "structures", column: "INCLUDE" }}>
                  <FieldCheck
                    label={included ? "built" : "left out"}
                    ariaLabel={`Include structure ${group.name} in build`}
                    checked={included}
                    onChange={(v) => setGroupIncluded(group.name, v)}
                  />
                </Field>
                <Field label="Colour">
                  <GroupColourInput name={group.name} />
                </Field>
              </>
            ),
          },
        ]}
      />
    </div>
  );
};
