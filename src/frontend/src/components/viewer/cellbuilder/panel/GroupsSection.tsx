/**
 * The Build tab's cell GROUPS.
 *
 * Owns: per-group blueprints. A group is one structure compiled with its own
 * blueprint; only meaningful for engines that advertise `supports_grouping`
 * (driven off the flag, never a hardcoded slug).
 */

import React from "react";

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {Section, btnGray, inputCls} from "./chrome";

export const GroupsSection: React.FC = () => {
  const s = useCellBuilderStore();
  const eng = s.engines.find((e) => e.slug === s.selectedEngine);
  const supportsGrouping = Boolean(eng?.supports_grouping);
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
          {s.groups.map((g) => (
            <div key={g.name} className="flex items-center gap-1">
              <input
                type="text"
                className={`${inputCls} flex-1 min-w-0`}
                defaultValue={g.name}
                key={g.name}
                onBlur={(e) => s.renameGroup(g.name, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                }}
                title="Group name (also the compiled structure name)"
              />
              <select
                className={`${inputCls} min-w-0`}
                value={g.blueprint}
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
                className={btnGray}
                onClick={() => s.removeGroup(g.name)}
                title="Delete group (unassigns its cells)"
              >
                ✕
              </button>
            </div>
          ))}
          <button className={btnGray} onClick={() => s.addGroup()}>
            + Add group
          </button>
        </>
      )}
    </Section>
  );
};
