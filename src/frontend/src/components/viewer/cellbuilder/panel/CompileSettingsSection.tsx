/**
 * The Build tab's COMPILE SETTINGS.
 *
 * Owns: the engine, structural blueprint, its advertised parameters (generated
 * from the blueprint's `fields`), the design ruleset, the detailing engine and
 * the auto-compile toggle. Engine/detailing are compile-time choices; the
 * blueprint and its options round-trip through the document.
 */

import React from "react";

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {Section, inputCls} from "./chrome";

export const CompileSettingsSection: React.FC = () => {
  const s = useCellBuilderStore();
  return (
    <Section title="Compile settings">
      <label
        className="flex items-center gap-1"
        title={
          s.engines.find((e) => e.slug === s.selectedEngine)
            ?.description ??
          "Procedural engine that compiles the model (built-in, or a registered external engine)"
        }
      >
        <span className="whitespace-nowrap">Engine</span>
        <select
          className={`${inputCls} flex-1 min-w-0`}
          value={s.selectedEngine}
          onChange={(e) => s.setSelectedEngine(e.target.value)}
        >
          {s.engines.length === 0 && (
            <option value={s.selectedEngine}>{s.selectedEngine}</option>
          )}
          {s.engines.map((e) => (
            <option
              key={e.slug}
              value={e.slug}
              title={e.description ?? undefined}
            >
              {e.name} ({e.origin})
            </option>
          ))}
        </select>
      </label>
      <label
        className="flex items-center gap-1"
        title={
          s.blueprints.find((b) => b.slug === s.selectedBlueprint)
            ?.description ??
          "Structural blueprint the selected engine compiles the cells with (sets doc.blueprint_name)"
        }
      >
        <span className="whitespace-nowrap">Blueprint</span>
        <select
          className={`${inputCls} flex-1 min-w-0`}
          value={s.selectedBlueprint ?? ""}
          onChange={(e) => s.setSelectedBlueprint(e.target.value)}
        >
          {s.blueprints.length === 0 && (
            <option value={s.selectedBlueprint ?? ""}>
              {s.selectedBlueprint ?? "steel_stru"}
            </option>
          )}
          {s.blueprints.map((b) => (
            <option key={b.slug} value={b.slug} title={b.description}>
              {b.name}
            </option>
          ))}
        </select>
      </label>

      {/* Advertised blueprint parameters (doc.blueprint) — generated from
          the selected blueprint's `fields`. For steel_stru these are the
          girder/column/stringer section enums: pick a `BG…` box or `TUB…`
          tube to build the frame in box beams instead of I-beams. */}
      {(() => {
        const bp = s.blueprints.find(
          (b) => b.slug === s.selectedBlueprint,
        );
        const fields = bp?.fields ?? [];
        if (fields.length === 0) return null;
        return (
          <div className="flex flex-col gap-1 pl-2 ml-1 border-l border-gray-600/40">
            {fields.map((f) => {
              const cur = s.blueprintOptions[f.name] ?? f.default;
              const label =
                (f.label ?? f.name) + (f.unit ? ` (${f.unit})` : "");
              const title = `Sets doc.blueprint.${f.name}`;
              if (f.type === "enum") {
                return (
                  <label
                    key={f.name}
                    className="flex items-center gap-1"
                    title={title}
                  >
                    <span className="whitespace-nowrap text-gray-300">
                      {label}
                    </span>
                    <select
                      className={`${inputCls} flex-1 min-w-0`}
                      value={String(cur)}
                      onChange={(e) =>
                        s.setBlueprintOption(f.name, e.target.value)
                      }
                    >
                      {(f.options ?? []).map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </select>
                  </label>
                );
              }
              if (f.type === "bool") {
                return (
                  <label
                    key={f.name}
                    className="flex items-center gap-1 cursor-pointer text-gray-300"
                    title={title}
                  >
                    <input
                      type="checkbox"
                      className="accent-blue-600"
                      checked={Boolean(cur)}
                      onChange={(e) =>
                        s.setBlueprintOption(f.name, e.target.checked)
                      }
                    />
                    <span>{label}</span>
                  </label>
                );
              }
              return (
                <label
                  key={f.name}
                  className="flex items-center gap-1 text-gray-300"
                  title={title}
                >
                  <span className="whitespace-nowrap">{label}</span>
                  <input
                    type="number"
                    className={inputCls}
                    value={
                      Number.isFinite(Number(cur)) ? Number(cur) : ""
                    }
                    min={f.min}
                    max={f.max}
                    step="any"
                    onChange={(e) => {
                      const n = Number(e.target.value);
                      if (Number.isFinite(n))
                        s.setBlueprintOption(f.name, n);
                    }}
                  />
                </label>
              );
            })}
          </div>
        );
      })()}
      <label
        className="flex items-center gap-1"
        title={
          s.designRulesets.find((r) => r.slug === s.designRules)
            ?.description ??
          "Routing/penetration ruleset applied when the model compiles"
        }
      >
        <span className="whitespace-nowrap">Design rules</span>
        <select
          className={`${inputCls} flex-1 min-w-0`}
          value={s.designRules}
          onChange={(e) => s.setDesignRules(e.target.value)}
        >
          {s.designRulesets.length === 0 && (
            <option value={s.designRules}>{s.designRules}</option>
          )}
          {s.designRulesets.map((r) => (
            <option key={r.slug} value={r.slug} title={r.description}>
              {r.name} ({r.origin})
            </option>
          ))}
        </select>
      </label>
      <label
        className="flex items-center gap-1"
        title={
          s.detailingEngines.find((d) => d.slug === s.selectedDetailing)
            ?.description ??
          "Detailing engine — the fabrication-detail stage that adds connection joints after the structural compile (none = structural-only)"
        }
      >
        <span className="whitespace-nowrap">Detailing</span>
        <select
          className={`${inputCls} flex-1 min-w-0`}
          value={s.selectedDetailing}
          onChange={(e) => s.setSelectedDetailing(e.target.value)}
        >
          {s.detailingEngines.length === 0 && (
            <option value={s.selectedDetailing}>
              {s.selectedDetailing}
            </option>
          )}
          {s.detailingEngines.map((d) => (
            <option key={d.slug} value={d.slug} title={d.description}>
              {d.name} ({d.origin})
            </option>
          ))}
        </select>
      </label>
      <label
        className="flex items-center gap-1"
        title="Replace equipment boxes in the compiled model with the actual CAD geometry (for catalog equipment that have a linked CAD asset)"
      >
        <input
          type="checkbox"
          checked={s.equipmentCad}
          onChange={(e) => s.setEquipmentCad(e.target.checked)}
        />
        Use CAD models for equipment
      </label>
      <label
        className="flex items-center gap-1"
        title="Compile automatically after each commit"
      >
        <input
          type="checkbox"
          checked={s.autoCompile}
          onChange={(e) => s.setAutoCompile(e.target.checked)}
        />
        Auto-compile after commit
      </label>
    </Section>
  );
};
