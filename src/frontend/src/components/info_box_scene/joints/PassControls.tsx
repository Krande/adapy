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

import { originsInResult, useClashCheckStore, type ClashPassReport } from "@/state/clashCheckStore";

/** Core's own passes, which every deployment can run. A pass a plugin contributed appears here
 *  once a result has reported it -- the registry lives on the worker, so the panel learns of one
 *  by seeing it, rather than by guessing at what might be installed. */
const CORE_PASSES: readonly { name: string; label: string; hint: string }[] = [
  { name: "beam-beam", label: "beam ↔ beam", hint: "Members meeting at a shared node. Axes only — no CAD kernel." },
  { name: "plate-beam", label: "plate ↔ beam", hint: "Beams landing on a plate. Needs a CAD backend." },
  { name: "plate-plate", label: "plate ↔ plate", hint: "Plates meeting edge-on or mid-span. Needs a CAD backend." },
];

function labelFor(name: string, reported: ClashPassReport | undefined): string {
  const core = CORE_PASSES.find((p) => p.name === name);
  if (core) return core.label;
  return reported?.capability ? `${name} (${reported.capability})` : name;
}

/** Checkboxes choosing which passes the next run performs. */
export const PassSelector: React.FC = () => {
  const selected = useClashCheckStore((s) => s.selectedPasses);
  const setSelectedPasses = useClashCheckStore((s) => s.setSelectedPasses);
  const result = useClashCheckStore((s) => s.result);

  // Core's three, plus any pass a previous result mentioned -- which is how a plugin's pass
  // becomes offerable without the panel having to know what is installed.
  const reported = result?.passes ?? [];
  const names = [...CORE_PASSES.map((p) => p.name)];
  for (const p of reported) if (!names.includes(p.name)) names.push(p.name);

  // `null` means "core's default set", which is what omitting the field on the wire means. It is
  // NOT the same as an empty selection, which is a user asking for nothing.
  const isOn = (name: string) =>
    selected === null ? CORE_PASSES.some((p) => p.name === name) : selected.includes(name);

  const toggle = (name: string) => {
    const current = selected === null ? CORE_PASSES.map((p) => p.name) : [...selected];
    const next = current.includes(name) ? current.filter((n) => n !== name) : [...current, name];
    setSelectedPasses(next);
  };

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="clash-pass-selector">
      <span className="text-[11px] text-gray-400">look for</span>
      {names.map((name) => {
        const entry = reported.find((p) => p.name === name);
        const core = CORE_PASSES.find((p) => p.name === name);
        return (
          <label
            key={name}
            className="flex items-center gap-1 text-[11px] text-gray-300"
            title={core?.hint ?? (entry?.capability ? `Runs on a worker advertising "${entry.capability}".` : name)}
          >
            <input type="checkbox" checked={isOn(name)} onChange={() => toggle(name)} />
            {labelFor(name, entry)}
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
