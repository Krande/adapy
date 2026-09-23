import React, { useMemo, useState } from "react";

import { useStatsStore } from "@/state/statsStore";
import {
  mergeProducedJoints,
  useClashCheckStore,
  type ProducedJointItem,
  type ProducedJoints,
} from "@/state/clashCheckStore";
import IdentifiedJoints from "./joints/IdentifiedJoints";

// Scene-panel "Joints" mode: a read-only overview of the joints this model knows about, from
// BOTH ends of the identify-then-detail workflow (`notes_core_asset_browser.md` §Decision 10's
// naming paragraph: "Identification and review are the two ends of one workflow; the cleanest
// strip is Clashes -> Joints, with a run's identified-and-grouped joints shown in Joints beside
// the produced ones rather than as a second joints table under Clashes.").
//
//   PRODUCED  -- "was detailed". The original section, unchanged: the `.stats.json` take-off's
//               `joints` block, present only for a model compiled with a detailing engine. A
//               produced joint has real plates/welds counts because something already built it.
//   IDENTIFIED -- "could be detailed". New: the `Clashes` tab's last result (`clashCheckStore`),
//               read the same one-derived-view way that tab reads it -- nothing here recomputes a
//               fact the store already carries. An identified joint has no plate/weld counts (it
//               is a candidate, not a build) and instead shows which registered specs could
//               detail it, so the two sections can never be mistaken for one another even without
//               reading their headings.
//
// BOTH SECTIONS ARE GROUPED BY CONNECTION TYPE, collapsed. A frame's joints run to the hundreds
// and a flat table of them is a scroll, not a review: the thing a person reads is "how many of
// each KIND, and where" -- which is the same unit the Clashes tab's rows use, so the two tabs
// fold the same way and an identified group keeps the colour its markers have in the 3D view.
//
// Both are REVIEW surfaces; the cellbuilder's Detailing tab remains the AUTHORING surface. The
// tab itself is available when EITHER section has something to show (`SceneInfoBox.tsx`'s
// `hasJoints = hasProducedJoints || hasIdentifiedJoints`), so this component must not go back to
// its old all-or-nothing empty state -- each section renders (or doesn't) on its own account.
//
// Selector discipline: `useStatsStore` returns the STORED `joints` object (a nested ref of the
// stored `stats`) and `useClashCheckStore` returns the STORED `result`, never a freshly-built
// array/object from either selector -- so neither can trip the unstable-selector infinite-render
// crash. Anything derived from `result` (there is only the flat `joints` list here) reads a field
// already computed once at parse time in `clashCheckStore.ts`, not a fresh array per render.

const fmt = (n: number) => (Number.isFinite(n) ? n.toFixed(2) : "—");

const Collapsible: React.FC<{
  open: boolean;
  onToggle: () => void;
  label: string;
  count: number;
  swatch?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}> = ({ open, onToggle, label, count, swatch, action, children }) => (
  <div className="border-t border-gray-700">
    <div className="flex items-center gap-2 px-1 py-1">
      {swatch && (
        <span
          className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: swatch }}
          title="This group's colour in the 3D view"
        />
      )}
      <button type="button" className="flex-1 min-w-0 text-left text-xs text-gray-100 hover:text-white truncate" onClick={onToggle} title={label}>
        {open ? "▼" : "▶"} {label}
      </button>
      <span className="text-xs text-gray-400 tabular-nums">{count}</span>
      {action}
    </div>
    {open && <div className="pb-1">{children}</div>}
  </div>
);

/** Produced joints, folded by the take-off's own type slug. The roll-up `by_type` carries the
 *  display name and the count; `items` carries the instances. Reading the instances through the
 *  roll-up (rather than grouping `items` and inventing names from `it.type`) keeps one source for
 *  "what types are there", which is the document's own answer. */
function producedByType(items: readonly ProducedJointItem[]): ReadonlyMap<string, readonly ProducedJointItem[]> {
  const out = new Map<string, ProducedJointItem[]>();
  for (const item of items) {
    const bucket = out.get(item.slug) ?? [];
    bucket.push(item);
    out.set(item.slug, bucket);
  }
  return out;
}

const ProducedSection: React.FC = () => {
  // TWO sources, one table. The loaded model's own take-off is there when it was COMPILED with a
  // detailing engine; a detail run generated here produces its own take-off beside the GLB it
  // overlays (`ProducedJoints` in `clashCheckStore`). Reading only the first is why a model whose
  // joints had just been generated showed the geometry and an empty table -- an overlay is not
  // the loaded model and has no stats of its own.
  const compiled = useStatsStore((s) => s.stats?.joints);
  const generated = useClashCheckStore((s) => s.producedJoints);
  const skipped = useClashCheckStore((s) => s.producedSkipped);
  const [open, setOpen] = useState<string | null>(null);
  const joints = useMemo<ProducedJoints | null>(
    () => mergeProducedJoints((compiled as ProducedJoints | undefined) ?? null, generated),
    [compiled, generated],
  );
  const bySlug = useMemo(() => producedByType(joints?.items ?? []), [joints]);
  if (!joints || joints.count === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <div className="text-gray-300">
        <span className="font-semibold">{joints.count}</span> produced joint
        {joints.count === 1 ? "" : "s"} <span className="text-gray-500">— was detailed</span>
      </div>
      {skipped.length > 0 && (
        <div className="text-[11px] text-amber-200/80" title={skipped.join("\n")}>
          {skipped.length} joint{skipped.length === 1 ? " was" : "s were"} skipped by their generator — hover for why
        </div>
      )}

      <div className="flex flex-col">
        {joints.by_type.map((t) => (
          <Collapsible
            key={t.slug}
            open={open === t.slug}
            onToggle={() => setOpen(open === t.slug ? null : t.slug)}
            label={t.name}
            count={t.count}
          >
            <div className="overflow-x-auto">
              <table className="w-full text-[11px] border-collapse">
                <thead>
                  <tr className="text-gray-400 text-left">
                    <th className="pr-2 font-medium">Joint</th>
                    <th className="pr-2 font-medium">Members</th>
                    <th className="pr-1 font-medium text-right">Pl</th>
                    <th className="pr-1 font-medium text-right">Wl</th>
                    <th className="pr-2 font-medium text-right">X</th>
                    <th className="pr-2 font-medium text-right">Y</th>
                    <th className="pr-0 font-medium text-right">Z</th>
                  </tr>
                </thead>
                <tbody>
                  {(bySlug.get(t.slug) ?? []).map((it) => (
                    <tr key={it.name} className="border-t border-white/10 align-top">
                      <td className="pr-2 py-0.5 whitespace-nowrap">{it.name}</td>
                      <td className="pr-2 py-0.5 text-gray-400 max-w-[10rem] truncate" title={it.members.join(", ")}>
                        {it.members.join(", ")}
                      </td>
                      <td className="pr-1 py-0.5 text-right tabular-nums">{it.plates}</td>
                      <td className="pr-1 py-0.5 text-right tabular-nums">{it.welds}</td>
                      <td className="pr-2 py-0.5 text-right tabular-nums text-gray-400">{it.centre ? fmt(it.centre[0]) : "—"}</td>
                      <td className="pr-2 py-0.5 text-right tabular-nums text-gray-400">{it.centre ? fmt(it.centre[1]) : "—"}</td>
                      <td className="pr-0 py-0.5 text-right tabular-nums text-gray-400">{it.centre ? fmt(it.centre[2]) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Collapsible>
        ))}
      </div>
    </div>
  );
};

const JointsOverviewPanel: React.FC = () => {
  const hasCompiled = useStatsStore((s) => (s.stats?.joints?.count ?? 0) > 0);
  const hasGenerated = useClashCheckStore((s) => (s.producedJoints?.count ?? 0) > 0);
  const hasProduced = hasCompiled || hasGenerated;
  const hasIdentified = useClashCheckStore((s) => (s.result?.joints.length ?? 0) > 0);

  if (!hasProduced && !hasIdentified) {
    // Reachable only for an instant while the tab is switching away (`SceneInfoBox` gates the
    // tab itself on this same pair of facts) -- kept as a real empty state anyway rather than
    // rendering nothing, so a direct `setMode('joints')` call from elsewhere is never blank.
    return (
      <p className="p-1 text-xs italic text-gray-400">
        No joints yet. Run a check in the Clashes tab to identify joints, or compile with a
        detailing engine (Compile settings ▸ Detailing) to produce them.
      </p>
    );
  }

  return (
    <div className="p-1 text-sm flex flex-col gap-4">
      <ProducedSection />
      <IdentifiedJoints />
    </div>
  );
};

export default JointsOverviewPanel;
