import React from "react";

import { useStatsStore } from "@/state/statsStore";
import {
  activeDisciplineCount,
  countSummary,
  fmt0,
  fmt1,
  fmtCog,
  massShare,
  type ModelStats,
} from "@/utils/stats/modelStats";
import ModelStatsPanel from "./ModelStatsPanel";

// The compact "Stats" card in the scene menu: total mass, object count, inline
// COG, a discipline mass-share bar + legend, and a top-right expand button that
// opens the detailed take-off panel. Wired to the fetched take-off; degrades to
// a muted note for models with no take-off (a capability engine / STEP-IFC imports).

function ExpandIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
    </svg>
  );
}

function CompactCard({ stats }: { stats: ModelStats }) {
  const openDetail = useStatsStore((s) => s.openDetail);
  const share = React.useMemo(() => massShare(stats), [stats]);
  const counts = React.useMemo(() => countSummary(stats), [stats]);
  const totalMass = React.useMemo(() => stats.disciplines.reduce((s, d) => s + (d.mass || 0), 0), [stats]);

  return (
    <div className="rounded-sm border border-gray-700/60 bg-gray-800/40 p-2 space-y-2">
      {/* header */}
      <div className="flex items-center gap-2">
        <span className="text-[12px] font-semibold tracking-wide">Stats</span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={openDetail}
          title="Open detailed statistics"
          aria-label="Open detailed statistics"
          className="shrink-0 grid place-items-center w-6 h-6 rounded-sm border border-gray-700/70 bg-gray-800/60 text-gray-300 hover:text-white hover:bg-gray-700"
        >
          <ExpandIcon />
        </button>
      </div>

      {/* hero */}
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <div className="text-[9.5px] uppercase tracking-wider text-gray-400 font-semibold">Total mass</div>
          <div className="text-2xl font-semibold font-mono tabular-nums leading-none">
            {fmt1(totalMass)}
            <span className="text-xs font-normal text-gray-400 ml-0.5">t</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-[9.5px] uppercase tracking-wider text-gray-400 font-semibold">Objects</div>
          <div className="text-lg font-semibold font-mono tabular-nums leading-none">{fmt0(stats.objects)}</div>
        </div>
      </div>

      {/* inline COG */}
      <div className="flex gap-1.5" title="Total centre of gravity (m)">
        {(["X", "Y", "Z"] as const).map((ax, i) => (
          <div key={ax} className="flex-1 rounded-sm border border-gray-700/60 bg-gray-800/50 px-1.5 py-1 text-center">
            <div className="text-[8.5px] uppercase tracking-wider text-gray-500 font-bold">COG {ax}</div>
            <div className="text-[12px] font-semibold font-mono tabular-nums">{fmtCog(stats.total_cog[i])}</div>
          </div>
        ))}
      </div>

      {/* mass-share bar */}
      <div className="flex h-2 rounded overflow-hidden bg-gray-700/50" role="img" aria-label="Mass share by discipline">
        {share.map((s) => (
          <span key={s.key} style={{ width: `${s.pct}%`, background: s.color }} title={`${s.name} ${fmt1(s.mass)}t`} />
        ))}
      </div>

      {/* legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {share.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5 text-[11px] text-gray-400">
            <span className="w-2 h-2 rounded-sm" style={{ background: s.color }} />
            {s.name} <b className="text-gray-200 font-semibold font-mono">{fmt1(s.mass)}t</b>
          </span>
        ))}
      </div>

      {/* counts */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 pt-1.5 border-t border-gray-700/50 text-[11px] text-gray-400">
        <span><b className="text-gray-200 font-mono">{fmt0(counts.beams)}</b> beams</span>
        <span><b className="text-gray-200 font-mono">{fmt0(counts.plates)}</b> plates</span>
        <span><b className="text-gray-200 font-mono">{fmt0(counts.pipeSeg)}</b> pipe seg</span>
        <span><b className="text-gray-200 font-mono">{fmt0(counts.ductSeg)}</b> duct seg</span>
        <span><b className="text-gray-200 font-mono">{fmt0(counts.traySeg)}</b> tray seg</span>
        <span><b className="text-gray-200 font-mono">{activeDisciplineCount(stats)}</b> disc.</span>
      </div>
    </div>
  );
}

const ModelStatsSection = () => {
  const loading = useStatsStore((s) => s.loading);
  const available = useStatsStore((s) => s.available);
  const stats = useStatsStore((s) => s.stats);

  if (loading && !stats) {
    return <div className="text-xs italic opacity-70 py-1">Computing take-off…</div>;
  }
  if (!available || !stats) {
    return (
      <div className="text-xs italic opacity-70 py-1">
        Take-off not available for this model.
      </div>
    );
  }
  return (
    <>
      <CompactCard stats={stats} />
      <ModelStatsPanel />
    </>
  );
};

export default ModelStatsSection;
