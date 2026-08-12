import React from "react";

import { useStatsStore } from "@/state/statsStore";

// Scene-panel "Joints" mode: a read-only overview of the fabrication-detail
// connection joints in the loaded procedural model. The data comes from the
// same `.stats.json` take-off the "Take-off" section reads — its `joints` block
// (present only for models compiled with a detailing engine): a per-type roll-up
// (the same counts the Detailing tab badges) plus a per-instance table (type,
// framed members, plate/weld counts, node location).
//
// This is a REVIEW surface (what the detailing stage produced), distinct from the
// cellbuilder's Detailing tab which is the AUTHORING surface (pick engine + joint
// options). It appears as a contextual tab only when the model carries joints.
//
// Selector discipline: the single `useStatsStore` selector returns the STORED
// `joints` object (a nested ref of the stored `stats`), never a freshly-built
// array/object — so it can't trip the unstable-selector infinite-render crash.

const JointsOverviewPanel: React.FC = () => {
  const joints = useStatsStore((s) => s.stats?.joints);

  if (!joints || joints.count === 0) {
    return (
      <p className="p-1 text-xs italic text-gray-400">
        No detailing joints in this model. Compile with a detailing engine
        (Compile settings ▸ Detailing) to add connection joints.
      </p>
    );
  }

  const fmt = (n: number) => (Number.isFinite(n) ? n.toFixed(2) : "—");

  return (
    <div className="p-1 text-sm flex flex-col gap-2">
      <div className="text-gray-300">
        <span className="font-semibold">{joints.count}</span> connection joint
        {joints.count === 1 ? "" : "s"}
      </div>

      {/* Per-type roll-up */}
      <div className="flex flex-col gap-0.5">
        {joints.by_type.map((t) => (
          <div key={t.slug} className="flex items-center gap-2 text-gray-300">
            <span className="flex-1 truncate" title={t.slug}>
              {t.name}
            </span>
            <span className="tabular-nums text-gray-400">{t.count}</span>
          </div>
        ))}
      </div>

      {/* Per-instance table */}
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] border-collapse">
          <thead>
            <tr className="text-gray-400 text-left">
              <th className="pr-2 font-medium">Joint</th>
              <th className="pr-2 font-medium">Type</th>
              <th className="pr-2 font-medium">Members</th>
              <th className="pr-1 font-medium text-right">Pl</th>
              <th className="pr-1 font-medium text-right">Wl</th>
              <th className="pr-2 font-medium text-right">X</th>
              <th className="pr-2 font-medium text-right">Y</th>
              <th className="pr-0 font-medium text-right">Z</th>
            </tr>
          </thead>
          <tbody>
            {joints.items.map((it) => (
              <tr key={it.name} className="border-t border-white/10 align-top">
                <td className="pr-2 py-0.5 whitespace-nowrap">{it.name}</td>
                <td className="pr-2 py-0.5 text-gray-400">{it.type}</td>
                <td
                  className="pr-2 py-0.5 text-gray-400 max-w-[10rem] truncate"
                  title={it.members.join(", ")}
                >
                  {it.members.join(", ")}
                </td>
                <td className="pr-1 py-0.5 text-right tabular-nums">
                  {it.plates}
                </td>
                <td className="pr-1 py-0.5 text-right tabular-nums">
                  {it.welds}
                </td>
                <td className="pr-2 py-0.5 text-right tabular-nums text-gray-400">
                  {it.centre ? fmt(it.centre[0]) : "—"}
                </td>
                <td className="pr-2 py-0.5 text-right tabular-nums text-gray-400">
                  {it.centre ? fmt(it.centre[1]) : "—"}
                </td>
                <td className="pr-0 py-0.5 text-right tabular-nums text-gray-400">
                  {it.centre ? fmt(it.centre[2]) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default JointsOverviewPanel;
