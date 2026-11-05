/**
 * The Build tab's cell GROUPS.
 *
 * Owns: per-group blueprints, and per-group BUILD inclusion and DISPLAY. A group
 * is one structure compiled with its own blueprint; only meaningful for engines
 * that advertise `supports_grouping` (driven off the flag, never a hardcoded
 * slug -- an engine without it, e.g. adapy-default, gets none of these controls).
 *
 * Two kinds of control per group, kept apart on purpose:
 * - the INCLUDE switch is a document edit (`include: false` on the group):
 *   undoable, marks the model dirty, takes effect on the next compile;
 * - the eye, colour swatch and Isolate are display only (`groupViewStore` +
 *   `groupView.ts`): instant, on the cell boxes and the compiled result alike,
 *   never saved and never a reason to recompile.
 */

import React, {useEffect} from "react";

import {EyeGlyph} from "@/components/tree_view/treeGlyphs";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {coloursOf, useGroupViewStore} from "@/state/cellbuilder/groupViewStore";
import {groupColor, isGroupIncluded} from "@/utils/cellbuilder/groups";
import {installGroupView, isolateGroup, setGroupHidden, showAllGroups} from "@/utils/cellbuilder/groupView";
import {Section, btnGray, inputCls} from "./chrome";

export const GroupsSection: React.FC = () => {
  const s = useCellBuilderStore();
  const eng = s.engines.find((e) => e.slug === s.selectedEngine);
  const supportsGrouping = Boolean(eng?.supports_grouping);
  const colourByGroup = useGroupViewStore((v) => v.colourByGroup);
  const setColourByGroup = useGroupViewStore((v) => v.setColourByGroup);
  const hiddenGroups = useGroupViewStore((v) => v.hiddenGroups);
  const overrides = useGroupViewStore((v) => coloursOf(v, s.active?.modelId));
  const setGroupColour = useGroupViewStore((v) => v.setGroupColour);
  useEffect(() => installGroupView(), []);

  const rename = (from: string, to: string) => {
    s.renameGroup(from, to);
    const name = to.trim();
    // The rename may be refused (blank / taken): only then is there nothing to carry over.
    if (s.active && useCellBuilderStore.getState().groups.some((g) => g.name === name) && name !== from)
      useGroupViewStore.getState().renameGroup(s.active.modelId, from, name);
  };

  return (
    <Section
      title="Groups"
      count={supportsGrouping ? s.groups.length : undefined}
    >
      {!supportsGrouping ? (
        <div className="text-gray-400 text-xs">
          {eng?.name ?? s.selectedEngine} compiles a single blueprint;
          grouping is available with a capability engine.
        </div>
      ) : (
        <>
          {s.groups.length === 0 && (
            <div className="text-gray-400 text-xs">
              No groups — every cell uses the model-level blueprint. Add
              a group to give a set of cells its own blueprint.
            </div>
          )}
          {s.groups.length > 0 && (
            <div className="flex items-center gap-2 text-xs text-gray-300">
              <label className="flex items-center gap-1 cursor-pointer" title="Tint the cells and the compiled structures by group (display only)">
                <input
                  type="checkbox"
                  aria-label="Colour by group"
                  checked={colourByGroup}
                  onChange={(e) => setColourByGroup(e.target.checked)}
                />
                Colour by group
              </label>
              <button
                className={`${btnGray} ml-auto text-xs`}
                aria-label="Show all groups"
                title="Show every group again (cells and compiled result)"
                onClick={() => showAllGroups()}
              >
                Show all
              </button>
            </div>
          )}
          {s.groups.map((g) => {
            const hidden = hiddenGroups.includes(g.name);
            const included = isGroupIncluded(g);
            const colour = groupColor(s.groups, g.name, overrides) ?? "#888888";
            return (
              <div
                key={g.name}
                role="group"
                aria-label={`Group ${g.name}`}
                className={`flex flex-col gap-1 border-l-2 pl-1 ${included ? "" : "opacity-60"}`}
                style={{borderColor: colourByGroup ? colour : "transparent"}}
              >
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    className="shrink-0 grid place-items-center rounded-sm px-0.5 text-gray-200 hover:bg-white/10"
                    aria-label={hidden ? `Show group ${g.name}` : `Hide group ${g.name}`}
                    aria-pressed={hidden}
                    title={
                      hidden
                        ? "Hidden -- click to show its cells and compiled structure"
                        : "Visible -- click to hide its cells and compiled structure (display only, nothing is rebuilt)"
                    }
                    onClick={() => setGroupHidden(g.name, !hidden)}
                  >
                    <EyeGlyph shut={hidden} />
                  </button>
                  <input
                    type="color"
                    className="h-5 w-5 shrink-0 cursor-pointer bg-transparent border-0 p-0"
                    aria-label={`Colour of group ${g.name}`}
                    title="Group colour for Colour by group (display only)"
                    value={colour}
                    onChange={(e) => s.active && setGroupColour(s.active.modelId, g.name, e.target.value)}
                  />
                  <input
                    type="text"
                    className={`${inputCls} flex-1 min-w-0 ${included ? "" : "line-through"}`}
                    defaultValue={g.name}
                    key={g.name}
                    aria-label={`Name of group ${g.name}`}
                    onBlur={(e) => rename(g.name, e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") e.currentTarget.blur();
                    }}
                    title="Group name (also the compiled structure name)"
                  />
                  <label
                    className="flex items-center gap-0.5 text-xs text-gray-300 cursor-pointer shrink-0"
                    title={
                      included
                        ? "Built -- untick to leave this structure out of the next compile (saved with the model)"
                        : "Left out of the build -- tick to build it again (saved with the model; recompile to see it)"
                    }
                  >
                    <input
                      type="checkbox"
                      role="switch"
                      aria-label={`Include group ${g.name} in build`}
                      aria-checked={included}
                      checked={included}
                      onChange={(e) => s.setGroupIncluded(g.name, e.target.checked)}
                    />
                    Build
                  </label>
                </div>
                <div className="flex items-center gap-1 pl-5">
                  <select
                    className={`${inputCls} min-w-0 flex-1`}
                    value={g.blueprint}
                    aria-label={`Blueprint of group ${g.name}`}
                    onChange={(e) =>
                      s.setGroupBlueprint(g.name, e.target.value)
                    }
                    title="Structural blueprint this group compiles with"
                  >
                    {!s.blueprints.some((b) => b.slug === g.blueprint) && (
                      <option value={g.blueprint}>
                        {g.blueprint || "—"}
                      </option>
                    )}
                    {s.blueprints.map((b) => (
                      <option
                        key={b.slug}
                        value={b.slug}
                        title={b.description}
                      >
                        {b.name}
                      </option>
                    ))}
                  </select>
                  <button
                    className={`${btnGray} text-xs`}
                    aria-label={`Isolate group ${g.name}`}
                    onClick={() => isolateGroup(g.name)}
                    title="Show only this group (display only)"
                  >
                    Isolate
                  </button>
                  <button
                    className={btnGray}
                    aria-label={`Delete group ${g.name}`}
                    onClick={() => s.removeGroup(g.name)}
                    title="Delete group (unassigns its cells)"
                  >
                    ✕
                  </button>
                </div>
                {!included && (
                  <div className="text-[11px] text-amber-300 pl-5">
                    Left out of the build{s.dirty ? " — commit to rebuild without it" : ""}.
                  </div>
                )}
              </div>
            );
          })}
          <button className={btnGray} onClick={() => s.addGroup()}>
            + Add group
          </button>
        </>
      )}
    </Section>
  );
};
