// Which searches a check runs, and which producers its results show.
//
// TWO CONTROLS, TWO MOMENTS. The checkboxes choose what the NEXT run looks for; the filter
// chooses what THIS result shows. Keeping them apart matters: turning a producer off in the
// filter must not silently re-run anything, and unticking a pass must not retroactively edit a
// result that was produced with it.
//
// Passes are core's vocabulary -- names like `beam-beam`, plus whatever a plugin contributed
// under a capability. This component never learns which package contributed one; a pass that
// names a capability says which POOL can run it, which is the same convention `applicable`
// already follows for the generators that detail a joint.

import React from "react";

import { type WireClashCheckerOption, type WireClashPassSpec } from "@/services/api/clashCheck";
import {
  moveSpecProvider,
  specProviderChoices,
  specProviderLabel,
  toggleSpecProvider,
} from "@/services/clashSpecProviders";
import {
  BUILTIN_CHECKER,
  effectiveSpecProviders,
  originsInResult,
  useClashCheckStore,
  type ClashPassReport,
} from "@/state/clashCheckStore";

/** Core's own passes, which every deployment can run -- the fallback while the live listing
 *  (`GET …/clash-check/passes`) has not answered, or where a deployment predates it. */
const CORE_PASSES: readonly { name: string; label: string; hint: string }[] = [
  { name: "beam-beam", label: "beam ↔ beam", hint: "Members meeting at a shared node. Axes only — no CAD kernel." },
  { name: "plate-beam", label: "plate ↔ beam", hint: "Beams landing on a plate. Needs a CAD backend." },
  { name: "plate-plate", label: "plate ↔ plate", hint: "Plates meeting edge-on or mid-span. Needs a CAD backend." },
];

function labelFor(name: string, reported: ClashPassReport | undefined, advertised?: WireClashPassSpec): string {
  const core = CORE_PASSES.find((p) => p.name === name);
  if (core) return core.label;
  // A contributed pass names itself; the capability is appended because it is the only thing
  // core knows about where it came from, and a person choosing between two passes deserves it.
  const label = advertised?.label || name;
  const capability = advertised?.capability ?? reported?.capability;
  return capability ? `${label} (${capability})` : label;
}

/** The clash-check ENGINE the next run uses: core's, or one a live pool contributes. One per run,
 *  because two engines over one model report the same contacts twice under two origins. Hidden
 *  while core's is the only one there is -- a dropdown with one entry is not a choice. */
export const CheckerSelector: React.FC = () => {
  const checkers = useClashCheckStore((s) => s.availableCheckers);
  const checker = useClashCheckStore((s) => s.checker);
  const setChecker = useClashCheckStore((s) => s.setChecker);
  if (!checkers || checkers.length < 2) return null;
  const current = checkers.find((c) => c.name === checker);
  return (
    <div className="flex flex-col gap-0.5" data-testid="clash-checker-selector">
      <label className="flex items-center gap-1 text-[11px] text-gray-300">
        checker
        <select
          aria-label="Clash checker"
          className="bg-gray-600 text-white rounded-sm text-[11px] px-1 py-0.5"
          value={checker}
          onChange={(e) => setChecker(e.target.value)}
        >
          {checkers.map((c) => (
            <option key={c.name} value={c.name}>
              {c.label || c.name}
              {c.capability ? ` (${c.capability})` : ""}
            </option>
          ))}
        </select>
      </label>
      {current?.description && <span className="text-[10px] text-gray-500">{current.description}</span>}
    </div>
  );
};

/** The selected contributed checker's own settings, drawn from what it advertises. Core never
 *  learns what they mean; it carries them to the checker as `checker_options`. */
export const CheckerOptionsForm: React.FC = () => {
  const checkers = useClashCheckStore((s) => s.availableCheckers);
  const checker = useClashCheckStore((s) => s.checker);
  const typed = useClashCheckStore((s) => s.checkerOptions[s.checker]);
  const setCheckerOption = useClashCheckStore((s) => s.setCheckerOption);
  const spec = checkers?.find((c) => c.name === checker);
  if (!spec || checker === BUILTIN_CHECKER || !spec.options?.length) return null;

  const valueOf = (opt: WireClashCheckerOption) => (typed && opt.key in typed ? typed[opt.key] : opt.default);
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="clash-checker-options">
      {spec.options.map((opt) => {
        const label = `${opt.label || opt.key}${opt.unit ? ` [${opt.unit}]` : ""}`;
        const value = valueOf(opt);
        if (opt.type === "boolean") {
          return (
            <label key={opt.key} className="flex items-center gap-1 text-[11px] text-gray-300" title={opt.help}>
              <input type="checkbox" checked={Boolean(value)} onChange={(e) => setCheckerOption(opt.key, e.target.checked)} />
              {label}
            </label>
          );
        }
        const numeric = opt.type === "number" || opt.type === "integer";
        return (
          <label key={opt.key} className="flex items-center gap-1 text-[11px] text-gray-300" title={opt.help}>
            {label}
            <input
              type={numeric ? "number" : "text"}
              step={opt.type === "integer" ? 1 : "any"}
              min={opt.min}
              max={opt.max}
              className="w-24 bg-gray-600 text-white rounded-sm px-1 py-0.5"
              value={value === null || value === undefined ? "" : String(value)}
              onChange={(e) => {
                const raw = e.target.value;
                if (!numeric) return setCheckerOption(opt.key, raw);
                // Empty means "the checker's default", which `checkRequestOptions` sends as such.
                setCheckerOption(opt.key, raw === "" ? undefined : opt.type === "integer" ? Math.trunc(Number(raw)) : Number(raw));
              }}
            />
          </label>
        );
      })}
    </div>
  );
};

/** Which spec PROVIDERS detail the joints, in order of preference. The scope's admin default
 *  (`public.clash.spec_providers`), overridable here for the session without writing it back. */
export const SpecProviderSelector: React.FC = () => {
  const advertised = useClashCheckStore((s) => s.specProviders);
  const adminPref = useClashCheckStore((s) => s.adminSpecProviders);
  const override = useClashCheckStore((s) => s.specProvidersOverride);
  const setOverride = useClashCheckStore((s) => s.setSpecProvidersOverride);
  const pref = effectiveSpecProviders({ adminSpecProviders: adminPref, specProvidersOverride: override });
  const choices = specProviderChoices(advertised, pref);
  // Only core's own and nothing configured: there is nothing to choose between.
  if (choices.length < 2 && pref === null) return null;
  const enabled = choices.filter((c) => c.enabled).map((c) => c.provider);

  return (
    <div className="flex flex-wrap items-center gap-1 pb-1" data-testid="clash-spec-providers">
      <span className="text-[11px] text-gray-400" title="Which providers' specs detail the joints, first preferred">
        detail with
      </span>
      {choices.map((c) => {
        const rank = enabled.indexOf(c.provider);
        return (
          <span
            key={c.provider}
            className={`flex items-center gap-0.5 rounded-sm px-1 py-0.5 text-[10px] ${
              c.enabled ? "bg-emerald-900/60 text-emerald-100" : "bg-gray-700 text-gray-400"
            }`}
            title={c.advertised ? undefined : "No live pool advertises this provider right now"}
          >
            <input
              type="checkbox"
              aria-label={`Use ${specProviderLabel(c.provider)} specs`}
              checked={c.enabled}
              onChange={(e) => setOverride(toggleSpecProvider(pref, advertised, c.provider, e.target.checked))}
            />
            {c.enabled && enabled.length > 1 && <span className="text-gray-400">{rank + 1}.</span>}
            {specProviderLabel(c.provider)}
            {!c.advertised && <span className="text-amber-300">!</span>}
            {c.enabled && rank > 0 && (
              <button
                type="button"
                className="text-gray-400 hover:text-white"
                title="Prefer this provider over the one before it"
                onClick={() => setOverride(moveSpecProvider(enabled, c.provider, -1))}
              >
                ↑
              </button>
            )}
          </span>
        );
      })}
      {override !== undefined && (
        <button
          type="button"
          className="text-[10px] text-gray-400 hover:text-white"
          title="Go back to this scope's default, as set in Admin → Providers"
          onClick={() => setOverride(undefined)}
        >
          scope default
        </button>
      )}
    </div>
  );
};

/** Checkboxes choosing which passes the next run performs -- within the selected checker. */
export const PassSelector: React.FC = () => {
  const selected = useClashCheckStore((s) => s.selectedPasses);
  const setSelectedPasses = useClashCheckStore((s) => s.setSelectedPasses);
  const result = useClashCheckStore((s) => s.result);
  const available = useClashCheckStore((s) => s.availablePasses);
  const checker = useClashCheckStore((s) => s.checker);
  const checkers = useClashCheckStore((s) => s.availableCheckers);

  // A contributed checker owns its passes; core's runs everything nobody else owns. One pass is
  // not a choice, so a single-pass engine shows no checkboxes at all.
  const owned = new Set((checkers ?? []).filter((c) => c.name !== BUILTIN_CHECKER).flatMap((c) => c.passes ?? []));
  const mine = checker === BUILTIN_CHECKER ? null : new Set(checkers?.find((c) => c.name === checker)?.passes ?? []);
  const belongs = (name: string) => (mine ? mine.has(name) : !owned.has(name));

  // What the DEPLOYMENT says it can run (core's own unioned with what a live pool advertises),
  // falling back to core's three where that is not known. Plus anything a previous result
  // mentioned, so a pass stays visible even if the pool that offered it has since gone away --
  // otherwise a result's producer filter could name a pass its own checkbox had vanished.
  const reported = result?.checker === checker ? result.passes : [];
  const names: string[] = [];
  for (const p of available ?? []) if (!names.includes(p.name) && belongs(p.name)) names.push(p.name);
  if (names.length === 0 && !mine) names.push(...CORE_PASSES.map((p) => p.name));
  for (const p of reported) if (!names.includes(p.name) && belongs(p.name)) names.push(p.name);
  if (mine && names.length < 2) return null;

  // `null` means "core's default set", which is what omitting the field on the wire means. It is
  // NOT the same as an empty selection, which is a user asking for nothing.
  // For a contributed checker `null` means all of ITS passes.
  const defaults = mine ? [...mine] : CORE_PASSES.map((p) => p.name);
  const isOn = (name: string) => (selected === null ? defaults.includes(name) : selected.includes(name));

  const toggle = (name: string) => {
    const current = selected === null ? defaults : [...selected];
    const next = current.includes(name) ? current.filter((n) => n !== name) : [...current, name];
    setSelectedPasses(next);
  };

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="clash-pass-selector">
      <span className="text-[11px] text-gray-400">look for</span>
      {names.map((name) => {
        const entry = reported.find((p) => p.name === name);
        const advertised = (available ?? []).find((p) => p.name === name);
        const core = CORE_PASSES.find((p) => p.name === name);
        return (
          <label
            key={name}
            className="flex items-center gap-1 text-[11px] text-gray-300"
            title={
              core?.hint ??
              (advertised?.capability || entry?.capability
                ? `Runs on a worker advertising "${advertised?.capability ?? entry?.capability}".`
                : name)
            }
          >
            <input type="checkbox" checked={isOn(name)} onChange={() => toggle(name)} />
            {labelFor(name, entry, advertised)}
          </label>
        );
      })}
    </div>
  );
};

/** Filter hiding a producer's joints in the CURRENT result. */
export const OriginFilter: React.FC = () => {
  const result = useClashCheckStore((s) => s.result);
  const hidden = useClashCheckStore((s) => s.hiddenOrigins);
  const toggleOriginHidden = useClashCheckStore((s) => s.toggleOriginHidden);

  // Built from the joints, so a producer is offered exactly when it has something to hide. One
  // producer is not a filter -- there is nothing to choose between.
  const origins = React.useMemo(() => (result ? originsInResult(result) : []), [result]);
  if (origins.length < 2) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 pb-1" data-testid="clash-origin-filter">
      <span className="text-[11px] text-gray-400">found by</span>
      {origins.map(({ origin, count }) => (
        <label
          key={origin}
          className="flex items-center gap-1 text-[11px] text-gray-300"
          title={`${count} joint${count === 1 ? "" : "s"} found by ${origin}`}
        >
          <input
            type="checkbox"
            checked={!hidden.includes(origin)}
            onChange={() => toggleOriginHidden(origin)}
          />
          {origin}
          <span className="text-gray-500">{count}</span>
        </label>
      ))}
    </div>
  );
};
