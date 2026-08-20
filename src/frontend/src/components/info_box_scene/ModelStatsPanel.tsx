import React from "react";

import { useStatsStore } from "@/state/statsStore";
import {
  DISCIPLINE_META,
  DISCIPLINE_ORDER,
  fmt0,
  fmt1,
  fmtCog,
  massShare,
  sumBy,
  STATS_TABS,
  type ModelStats,
  type StatsTabKey,
} from "@/utils/stats/modelStats";

// The detailed take-off panel: a centered modal on desktop, a full-screen sheet
// on mobile. Tabs (Overview · COGs · Structural · Piping · HVAC · Electrical),
// per-discipline take-off tables, and an Export split-button (whole-model XLSX /
// active-tab CSV). Fixed-positioned so it overlays the viewport regardless of
// where the section mounts.

interface Col {
  key: string;
  label: string;
  first?: boolean; // left-aligned label column
}

function dig(row: Record<string, unknown>, key: string): React.ReactNode {
  const parts = key.split(".");
  let cur: unknown = row;
  for (const p of parts) {
    if (Array.isArray(cur)) cur = cur[Number(p)];
    else if (cur && typeof cur === "object") cur = (cur as Record<string, unknown>)[p];
    else return "";
  }
  if (typeof cur === "number") return cur.toLocaleString("en-US", { maximumFractionDigits: 4 });
  return (cur as React.ReactNode) ?? "";
}

function DataTable({ cols, rows, total }: { cols: Col[]; rows: Record<string, unknown>[]; total?: React.ReactNode[] }) {
  if (rows.length === 0) {
    return <div className="text-xs italic opacity-60 px-1 py-2">No items.</div>;
  }
  return (
    <div className="border border-edge rounded-md overflow-auto max-h-64">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            {cols.map((c) => (
              <th
                key={c.key}
                className={
                  "sticky top-0 bg-surface-0 text-content-muted font-semibold px-2.5 py-1.5 border-b border-edge whitespace-nowrap " +
                  (c.first ? "text-left" : "text-right")
                }
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="hover:bg-surface-0">
              {cols.map((c) => (
                <td
                  key={c.key}
                  className={
                    "px-2.5 py-1.5 border-b border-edge whitespace-nowrap " +
                    (c.first ? "text-left font-medium" : "text-right font-mono tabular-nums")
                  }
                >
                  {dig(r, c.key)}
                </td>
              ))}
            </tr>
          ))}
          {total && (
            <tr className="bg-surface-0 font-semibold text-content">
              {total.map((v, i) => (
                <td
                  key={i}
                  className={"px-2.5 py-1.5 whitespace-nowrap " + (i === 0 ? "text-left" : "text-right font-mono tabular-nums")}
                >
                  {v}
                </td>
              ))}
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Tile({ label, value, unit }: { label: string; value: React.ReactNode; unit?: string }) {
  return (
    <div className="bg-surface-1 border border-edge rounded-md px-2.5 py-2 min-w-0">
      <div className="text-[9.5px] uppercase tracking-wider text-content-muted font-semibold">{label}</div>
      <div className="text-lg font-semibold font-mono tabular-nums mt-0.5 truncate">
        {value}
        {unit && <span className="text-[11px] font-normal text-content-muted ml-0.5">{unit}</span>}
      </div>
    </div>
  );
}

function PaneTitle({ label, color }: { label: string; color?: string }) {
  return (
    <div className="flex items-center gap-2 text-[10.5px] uppercase tracking-wider text-content-muted font-bold">
      {color && <span className="w-2 h-2 rounded-sm" style={{ background: color }} />}
      {label}
    </div>
  );
}

const Tiles: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="grid gap-2 grid-cols-2 sm:grid-cols-4">{children}</div>
);

function OverviewPane({ stats }: { stats: ModelStats }) {
  const share = massShare(stats);
  const activeCount = share.filter((s) => s.mass > 0).length;
  const rows = share.map((s) => ({
    name: s.name,
    mass: s.mass,
    share: `${s.pct.toFixed(1)}%`,
    _color: s.color,
  }));
  return (
    <div className="flex flex-col gap-4">
      <Tiles>
        <Tile label="Total mass" value={fmt1(stats.total_mass)} unit=" t" />
        <Tile label="Objects" value={fmt0(stats.objects)} />
        <Tile label="Disciplines" value={String(activeCount)} />
        <Tile label="Bounding box" value={stats.bbox.map((v) => v.toFixed(1)).join(" × ")} unit=" m" />
      </Tiles>
      <div className="flex flex-col gap-2">
        <PaneTitle label="Mass by discipline" />
        <div className="flex h-3 rounded-md overflow-hidden bg-surface-2">
          {share.map((s) => (
            <span key={s.key} style={{ width: `${s.pct}%`, background: s.color }} title={s.name} />
          ))}
        </div>
        <DataTable
          cols={[
            { key: "name", label: "Discipline", first: true },
            { key: "mass", label: "Mass (t)" },
            { key: "share", label: "Share" },
          ]}
          rows={rows}
          total={["Total", fmt1(stats.total_mass), "100%"]}
        />
      </div>
    </div>
  );
}

function CogsPane({ stats }: { stats: ModelStats }) {
  const drows = DISCIPLINE_ORDER.map((k) => {
    const d = stats.disciplines.find((x) => x.key === k);
    return {
      name: DISCIPLINE_META[k].name,
      mass: d?.mass ?? 0,
      x: (d?.cog[0] ?? 0).toFixed(2),
      y: (d?.cog[1] ?? 0).toFixed(2),
      z: (d?.cog[2] ?? 0).toFixed(2),
    };
  });
  const mrows = stats.major_items.map((m) => ({
    name: m.name,
    discipline: DISCIPLINE_META[m.discipline as keyof typeof DISCIPLINE_META]?.name ?? m.discipline,
    mass: m.mass,
    x: m.cog[0].toFixed(2),
    y: m.cog[1].toFixed(2),
    z: m.cog[2].toFixed(2),
  }));
  return (
    <div className="flex flex-col gap-4">
      <Tiles>
        <div className="col-span-2 bg-surface-1 border border-edge rounded-md px-2.5 py-2">
          <div className="text-[9.5px] uppercase tracking-wider text-content-muted font-semibold">Total model COG (m)</div>
          <div className="flex gap-3 mt-1 font-mono tabular-nums text-sm">
            {(["X", "Y", "Z"] as const).map((ax, i) => (
              <span key={ax}>
                <span className="text-content-subtle text-[10px] font-bold mr-1">{ax}</span>
                {fmtCog(stats.total_cog[i])}
              </span>
            ))}
          </div>
        </div>
        <Tile label="Total mass" value={fmt1(stats.total_mass)} unit=" t" />
        <Tile label="Reference" value="global" />
      </Tiles>
      <div className="flex flex-col gap-2">
        <PaneTitle label="COG by discipline" />
        <DataTable
          cols={[
            { key: "name", label: "Discipline", first: true },
            { key: "mass", label: "Mass (t)" },
            { key: "x", label: "X (m)" },
            { key: "y", label: "Y (m)" },
            { key: "z", label: "Z (m)" },
          ]}
          rows={drows}
        />
      </div>
      <div className="flex flex-col gap-2">
        <PaneTitle label="Major items — mass & COG" />
        <DataTable
          cols={[
            { key: "name", label: "Item", first: true },
            { key: "discipline", label: "Discipline" },
            { key: "mass", label: "Mass (t)" },
            { key: "x", label: "X" },
            { key: "y", label: "Y" },
            { key: "z", label: "Z" },
          ]}
          rows={mrows}
        />
      </div>
    </div>
  );
}

function StructuralPane({ stats }: { stats: ModelStats }) {
  const s = stats.structural;
  return (
    <div className="flex flex-col gap-4">
      <PaneTitle label="Structural take-off" color={DISCIPLINE_META.structural.color} />
      <Tiles>
        <Tile label="Steel mass" value={fmt1(s.mass)} unit=" t" />
        <Tile label="Beam length" value={fmt0(sumBy(s.beams, (r) => r.length))} unit=" m" />
        <Tile label="Plate area" value={fmt0(sumBy(s.plates, (r) => r.area))} unit=" m²" />
        <Tile label="Parts" value={fmt0(sumBy(s.beams, (r) => r.count) + sumBy(s.plates, (r) => r.count))} />
      </Tiles>
      <div className="flex flex-col gap-2">
        <PaneTitle label="Beams by section" />
        <DataTable
          cols={[
            { key: "section", label: "Section", first: true },
            { key: "count", label: "Count" },
            { key: "length", label: "Length (m)" },
            { key: "mass", label: "Mass (t)" },
          ]}
          rows={s.beams as unknown as Record<string, unknown>[]}
          total={[
            "All sections",
            fmt0(sumBy(s.beams, (r) => r.count)),
            fmt1(sumBy(s.beams, (r) => r.length)),
            fmt1(sumBy(s.beams, (r) => r.mass)),
          ]}
        />
      </div>
      <div className="flex flex-col gap-2">
        <PaneTitle label="Plates by thickness" />
        <DataTable
          cols={[
            { key: "label", label: "Plate", first: true },
            { key: "count", label: "Count" },
            { key: "area", label: "Area (m²)" },
            { key: "mass", label: "Mass (t)" },
          ]}
          rows={s.plates as unknown as Record<string, unknown>[]}
          total={[
            "All plates",
            fmt0(sumBy(s.plates, (r) => r.count)),
            fmt0(sumBy(s.plates, (r) => r.area)),
            fmt1(sumBy(s.plates, (r) => r.mass)),
          ]}
        />
      </div>
    </div>
  );
}

function FittingsGrid({ fittings }: { fittings: { name: string; count: number }[] }) {
  if (fittings.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <PaneTitle label="Fittings" />
      <div className="grid gap-2 grid-cols-2 sm:grid-cols-4">
        {fittings.map((f) => (
          <div key={f.name} className="bg-surface-1 border border-edge rounded-md px-2.5 py-2">
            <div className="text-[11px] text-content-muted">{f.name}</div>
            <div className="text-base font-semibold font-mono tabular-nums mt-0.5">{fmt0(f.count)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PipingPane({ stats }: { stats: ModelStats }) {
  const p = stats.piping;
  return (
    <div className="flex flex-col gap-4">
      <PaneTitle label="Piping take-off" color={DISCIPLINE_META.piping.color} />
      <Tiles>
        <Tile label="Pipe mass" value={fmt1(p.mass)} unit=" t" />
        <Tile label="Total length" value={fmt0(sumBy(p.segments, (r) => r.length))} unit=" m" />
        <Tile label="Segments" value={fmt0(sumBy(p.segments, (r) => r.segments))} />
        <Tile label="Fittings" value={fmt0(sumBy(p.fittings, (r) => r.count))} />
      </Tiles>
      <div className="flex flex-col gap-2">
        <PaneTitle label="Segments by nominal bore" />
        <DataTable
          cols={[
            { key: "size", label: "Size", first: true },
            { key: "segments", label: "Segments" },
            { key: "length", label: "Length (m)" },
            { key: "mass", label: "Mass (t)" },
          ]}
          rows={p.segments as unknown as Record<string, unknown>[]}
          total={[
            "All sizes",
            fmt0(sumBy(p.segments, (r) => r.segments)),
            fmt1(sumBy(p.segments, (r) => r.length)),
            fmt1(sumBy(p.segments, (r) => r.mass)),
          ]}
        />
      </div>
      <FittingsGrid fittings={p.fittings} />
    </div>
  );
}

function HvacPane({ stats }: { stats: ModelStats }) {
  const h = stats.hvac;
  return (
    <div className="flex flex-col gap-4">
      <PaneTitle label="HVAC take-off" color={DISCIPLINE_META.hvac.color} />
      <Tiles>
        <Tile label="Duct mass" value={fmt1(h.mass)} unit=" t" />
        <Tile label="Total length" value={fmt0(sumBy(h.segments, (r) => r.length))} unit=" m" />
        <Tile label="Surface area" value={fmt0(sumBy(h.segments, (r) => r.area))} unit=" m²" />
        <Tile label="Segments" value={fmt0(sumBy(h.segments, (r) => r.segments))} />
      </Tiles>
      <div className="flex flex-col gap-2">
        <PaneTitle label="Duct segments by size" />
        <DataTable
          cols={[
            { key: "size", label: "Size", first: true },
            { key: "segments", label: "Segments" },
            { key: "length", label: "Length (m)" },
            { key: "area", label: "Area (m²)" },
            { key: "mass", label: "Mass (t)" },
          ]}
          rows={h.segments as unknown as Record<string, unknown>[]}
          total={[
            "All ducts",
            fmt0(sumBy(h.segments, (r) => r.segments)),
            fmt1(sumBy(h.segments, (r) => r.length)),
            fmt0(sumBy(h.segments, (r) => r.area)),
            fmt1(sumBy(h.segments, (r) => r.mass)),
          ]}
        />
      </div>
      <FittingsGrid fittings={h.fittings} />
    </div>
  );
}

function ElectricalPane({ stats }: { stats: ModelStats }) {
  const e = stats.electrical;
  return (
    <div className="flex flex-col gap-4">
      <PaneTitle label="Electrical take-off" color={DISCIPLINE_META.electrical.color} />
      <Tiles>
        <Tile label="E&I mass" value={fmt1(e.mass)} unit=" t" />
        <Tile label="Tray length" value={fmt0(sumBy(e.trays, (r) => r.length))} unit=" m" />
        <Tile label="Cable length" value={fmt0(sumBy(e.cables, (r) => r.length))} unit=" m" />
        <Tile label="Tray segments" value={fmt0(sumBy(e.trays, (r) => r.segments))} />
      </Tiles>
      <div className="flex flex-col gap-2">
        <PaneTitle label="Cable tray by width" />
        <DataTable
          cols={[
            { key: "size", label: "Width", first: true },
            { key: "segments", label: "Segments" },
            { key: "length", label: "Length (m)" },
            { key: "mass", label: "Mass (t)" },
          ]}
          rows={e.trays as unknown as Record<string, unknown>[]}
          total={[
            "All trays",
            fmt0(sumBy(e.trays, (r) => r.segments)),
            fmt1(sumBy(e.trays, (r) => r.length)),
            fmt1(sumBy(e.trays, (r) => r.mass)),
          ]}
        />
      </div>
      <div className="flex flex-col gap-2">
        <PaneTitle label="Cables by type" />
        <DataTable
          cols={[
            { key: "type", label: "Type", first: true },
            { key: "length", label: "Length (m)" },
          ]}
          rows={e.cables as unknown as Record<string, unknown>[]}
        />
      </div>
    </div>
  );
}

function Pane({ tab, stats }: { tab: StatsTabKey; stats: ModelStats }) {
  switch (tab) {
    case "overview":
      return <OverviewPane stats={stats} />;
    case "cogs":
      return <CogsPane stats={stats} />;
    case "structural":
      return <StructuralPane stats={stats} />;
    case "piping":
      return <PipingPane stats={stats} />;
    case "hvac":
      return <HvacPane stats={stats} />;
    case "electrical":
      return <ElectricalPane stats={stats} />;
    default:
      return null;
  }
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <path d="M12 3v12m0 0l4-4m-4 4l-4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
    </svg>
  );
}

const ModelStatsPanel = () => {
  const open = useStatsStore((s) => s.detailOpen);
  const stats = useStatsStore((s) => s.stats);
  const activeTab = useStatsStore((s) => s.activeTab);
  const exportMenuOpen = useStatsStore((s) => s.exportMenuOpen);
  const exporting = useStatsStore((s) => s.exporting);
  const setActiveTab = useStatsStore((s) => s.setActiveTab);
  const closeDetail = useStatsStore((s) => s.closeDetail);
  const setExportMenuOpen = useStatsStore((s) => s.setExportMenuOpen);
  const exportStats = useStatsStore((s) => s.exportStats);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDetail();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, closeDetail]);

  if (!open || !stats) return null;

  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-label="Model statistics">
      <div className="absolute inset-0 bg-black/55" onClick={closeDetail} />
      <div
        className={
          "absolute bg-[var(--ada-panel-bg)] text-[var(--ada-panel-text)] border border-[var(--ada-panel-border)] shadow-2xl flex flex-col overflow-hidden " +
          // mobile: full-screen sheet; desktop: centered modal
          "inset-0 sm:inset-auto sm:left-1/2 sm:top-1/2 sm:-translate-x-1/2 sm:-translate-y-1/2 " +
          "sm:w-[min(720px,calc(100%-44px))] sm:h-[min(560px,calc(100%-44px))] sm:rounded-lg"
        }
      >
        {/* header */}
        <div className="shrink-0 flex items-center gap-2.5 px-3 py-2.5 border-b border-edge">
          <div>
            <div className="text-sm font-semibold leading-tight">Model Statistics</div>
            <div className="text-[11px] text-content-muted">
              {(stats.source_name || "model")} · {fmt0(stats.objects)} objects
            </div>
          </div>
          <span className="flex-1" />
          {/* export split-button */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setExportMenuOpen(!exportMenuOpen)}
              aria-haspopup="true"
              aria-expanded={exportMenuOpen}
              disabled={exporting}
              className="inline-flex items-center gap-1.5 bg-accent hover:bg-accent text-white rounded-md text-xs font-semibold px-2.5 py-1.5 disabled:opacity-60"
            >
              <DownloadIcon /> Export <span aria-hidden="true" className="opacity-70">▾</span>
            </button>
            {exportMenuOpen && (
              <div className="absolute right-0 top-[calc(100%+6px)] min-w-40 bg-surface-0 border border-edge rounded-md shadow-xl p-1 z-10">
                <button
                  type="button"
                  onClick={() => void exportStats("xlsx")}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-surface-2 text-left"
                >
                  <span className="text-[10px] font-bold border border-edge rounded px-1 py-0.5 text-content">XLSX</span>
                  Excel workbook
                </button>
                <button
                  type="button"
                  onClick={() => void exportStats("csv")}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-surface-2 text-left"
                >
                  <span className="text-[10px] font-bold border border-edge rounded px-1 py-0.5 text-content">CSV</span>
                  Comma-separated
                </button>
                <div className="text-[11px] text-content-muted px-2 pt-1.5 mt-1 border-t border-edge">
                  XLSX = whole model · CSV = active tab.
                </div>
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={closeDetail}
            title="Close"
            aria-label="Close"
            className="shrink-0 grid place-items-center w-7 h-7 rounded-md border border-edge bg-surface-0 text-content hover:text-white hover:bg-surface-2"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="w-3.5 h-3.5">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        {/* tab strip */}
        <div className="shrink-0 flex gap-0.5 px-2 border-b border-edge overflow-x-auto" role="tablist">
          {STATS_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={activeTab === t.key}
              onClick={() => setActiveTab(t.key)}
              className={
                "whitespace-nowrap px-3 py-2 text-xs font-medium border-b-2 -mb-px flex items-center gap-1.5 " +
                (activeTab === t.key
                  ? "border-accent text-[var(--ada-panel-text)]"
                  : "border-transparent text-content-muted hover:text-[var(--ada-panel-text)]")
              }
            >
              {t.color && <span className="w-1.5 h-1.5 rounded-sm" style={{ background: t.color }} />}
              {t.label}
            </button>
          ))}
        </div>

        {/* body */}
        <div className="flex-1 overflow-auto p-3.5">
          <Pane tab={activeTab} stats={stats} />
        </div>
      </div>
    </div>
  );
};

export default ModelStatsPanel;
