import assert from "node:assert/strict";
import { test } from "node:test";

import {
  activeDisciplineCount,
  countSummary,
  disciplineColor,
  fmt0,
  fmt1,
  fmtCog,
  massShare,
  sumBy,
  type ModelStats,
} from "../../utils/stats/modelStats";

const STATS: ModelStats = {
  schema_version: 1,
  source_name: "TopoModelDemo",
  units: { length: "m", mass: "tonne", area: "m2" },
  objects: 72,
  total_mass: 12.7763,
  total_cog: [5.0, 2.5185, 1.4722],
  bbox: [10.0, 5.0, 3.0],
  disciplines: [
    { key: "structural", name: "Structural", mass: 12.7763, cog: [5.0, 2.5185, 1.4722], count: 72 },
    { key: "piping", name: "Piping", mass: 0, cog: [0, 0, 0], count: 0 },
    { key: "hvac", name: "HVAC", mass: 0, cog: [0, 0, 0], count: 0 },
    { key: "electrical", name: "Electrical", mass: 0, cog: [0, 0, 0], count: 0 },
  ],
  structural: {
    mass: 12.7763,
    beams: [
      { section: "HP140x8", count: 48, length: 240, mass: 2.365 },
      { section: "IPE200", count: 14, length: 70, mass: 1.4973 },
      { section: "HEB200", count: 6, length: 18, mass: 1.064 },
    ],
    plates: [{ label: "PL10", thickness: 0.01, count: 4, area: 100, mass: 7.85 }],
  },
  piping: { mass: 0, segments: [], fittings: [{ name: "Elbows", count: 0 }] },
  hvac: { mass: 0, segments: [], fittings: [] },
  electrical: { mass: 0, trays: [], cables: [] },
  major_items: [],
};

test("fmt1 / fmt0 / fmtCog formatting", () => {
  assert.equal(fmt1(404.94), "404.9");
  assert.equal(fmt1(12), "12.0");
  assert.equal(fmt0(1712), "1,712");
  assert.equal(fmtCog(23.14), "23.14");
  assert.equal(fmtCog(7.6), "7.60");
  // non-finite is coerced to 0, never NaN
  assert.equal(fmt1(NaN), "0.0");
  assert.equal(fmt0(Infinity), "0");
});

test("sumBy sums a numeric field", () => {
  assert.equal(sumBy(STATS.structural.beams, (r) => r.count), 68);
  assert.equal(sumBy(STATS.structural.beams, (r) => r.length), 328);
  assert.equal(sumBy([], (r: { x: number }) => r.x), 0);
});

test("massShare yields four segments summing to 100%", () => {
  const seg = massShare(STATS);
  assert.equal(seg.length, 4);
  assert.deepEqual(
    seg.map((s) => s.key),
    ["structural", "piping", "hvac", "electrical"],
  );
  // structural is the entire mass here
  assert.equal(Math.round(seg[0].pct), 100);
  assert.equal(seg[1].pct, 0);
  const total = seg.reduce((s, x) => s + x.pct, 0);
  assert.ok(Math.abs(total - 100) < 1e-9);
});

test("massShare avoids divide-by-zero on an empty model", () => {
  const empty = { ...STATS, disciplines: STATS.disciplines.map((d) => ({ ...d, mass: 0 })) };
  const seg = massShare(empty);
  assert.ok(seg.every((s) => s.pct === 0));
});

test("countSummary reads the take-off tables", () => {
  const c = countSummary(STATS);
  assert.equal(c.beams, 68);
  assert.equal(c.plates, 4);
  assert.equal(c.pipeSeg, 0);
  assert.equal(c.ductSeg, 0);
  assert.equal(c.traySeg, 0);
});

test("activeDisciplineCount counts disciplines with mass/objects", () => {
  assert.equal(activeDisciplineCount(STATS), 1);
});

test("disciplineColor maps known keys and falls back", () => {
  assert.equal(disciplineColor("structural"), "#f59e0b");
  assert.equal(disciplineColor("piping"), "#22d3ee");
  assert.equal(disciplineColor("hvac"), "#34d399");
  assert.equal(disciplineColor("electrical"), "#a78bfa");
  assert.equal(disciplineColor("nope"), "#94a3b8");
});
