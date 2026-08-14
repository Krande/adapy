/* Render the capacity run-status section to a standalone HTML page.
 *
 *  A full streaming run takes the better part of an hour, which is a slow loop
 *  for looking at a panel. This renders the states that matter side by side,
 *  against the same CSS the viewer ships, so the layout can be reviewed in a
 *  browser in a second:
 *
 *      npx tsx scripts/preview-run-status.tsx > ../../../preview.html
 *
 *  Not part of the build or the test suite — a development aid. */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import CapacityRunStatus from "../src/components/capacity/CapacityRunStatus";
import type { CapacityCalcProgress } from "../src/state/capacityResultsStore";

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(here, "..", "dist", "assets");
const cssFile = fs.readdirSync(dist).find((f) => f.endsWith(".css"));
const css = cssFile ? fs.readFileSync(path.join(dist, cssFile), "utf-8") : "";

const panel = (label: string, progress: CapacityCalcProgress) => `
  <figure>
    <figcaption>${label}</figcaption>
    <div class="rounded-sm border border-gray-700 bg-gray-900/95 text-gray-100 text-xs shadow-lg">
      <div class="flex items-center justify-between gap-2 border-b border-gray-700 px-3 py-2">
        <div class="font-semibold tracking-wide">Capacity</div>
      </div>
      ${renderToStaticMarkup(React.createElement(CapacityRunStatus, { progress }))}
      <div class="p-3 text-gray-500">Capacity check type / Result case / ...</div>
    </div>
  </figure>`;

const base: CapacityCalcProgress = {
  complete: false,
  phase: "checking",
  message: "Running DNV-RP-C201 stiffened-panel checks",
  cases_total: 84,
  cases_done: 32,
  cases_known: true,
  elapsed_s: 134,
  runs: [],
};

const panelRun = {
  id: "run-001",
  scope: "stiffened_panel",
  label: "DNV-RP-C201 stiffened panel",
  cases_total: 84,
  cases_ready: [] as string[],
  complete: false,
};
const girderRun = {
  id: "run-002",
  scope: "girder",
  label: "DNV-RP-C201 girder",
  cases_total: 84,
  cases_ready: [] as string[],
  complete: false,
};

const states: Array<[string, CapacityCalcProgress]> = [
  [
    "Starting — no case count yet",
    {
      ...base,
      message: "Reading the SIN and reconstructing capacity models",
      cases_known: false,
      cases_total: 0,
      cases_done: 0,
      elapsed_s: 18,
      prep: {
        complete: false,
        active: "Identifying panel groups (geometric reconstruction)",
        steps: [
          { label: "Copying SIN into viewer storage", done: true, elapsed_s: 2.1 },
          { label: "Baking FEA artefacts for the viewer", done: true, elapsed_s: 41.8 },
          {
            label: "Identifying panel groups (geometric reconstruction)",
            done: false,
            elapsed_s: 0,
          },
          {
            label: "Reconstructing atomic panel fields",
            done: false,
            elapsed_s: 0,
            depth: 1,
            completed: 812,
            total: 2140,
          },
        ],
      },
      runs: [
        { ...panelRun, started: false },
        { ...girderRun, started: false },
      ],
    },
  ],
  [
    "Running — panels checking, girders queued",
    {
      ...base,
      prep: {
        complete: false,
        active: "Recovering reusable stress fields for basic cases",
        steps: [
          { label: "Reading SIN (mesh + section/name records)", done: true, elapsed_s: 31.3 },
          { label: "Reconstructing capacity models from SIN", done: true, elapsed_s: 74.5 },
          {
            label: "Recovering reusable stress fields for basic cases",
            done: false,
            elapsed_s: 0,
            completed: 56,
            total: 100,
          },
        ],
      },
      runs: [
        { ...panelRun, started: true, cases_ready: Array.from({ length: 32 }, (_, i) => `${i}`) },
        { ...girderRun, started: false },
      ],
    },
  ],
  [
    "Girders done, panels still going",
    {
      ...base,
      cases_done: 130,
      cases_total: 168,
      message: "Running DNV-RP-C201 stiffened-panel checks",
      elapsed_s: 1490,
      prep: {
        complete: true,
        active: null,
        steps: [
          { label: "Reading SIN (mesh + section/name records)", done: true, elapsed_s: 31.3 },
          { label: "Batch-resolving combinations across girders", done: true, elapsed_s: 34.3 },
        ],
      },
      runs: [
        { ...panelRun, started: true, cases_ready: Array.from({ length: 46 }, (_, i) => `${i}`) },
        {
          ...girderRun,
          started: true,
          complete: true,
          elapsed_s: 242,
          cases_ready: Array.from({ length: 84 }, (_, i) => `g${i}`),
        },
      ],
    },
  ],
  [
    "Complete — collapsed, stays on screen",
    {
      ...base,
      complete: true,
      message: "",
      cases_done: 168,
      cases_total: 168,
      elapsed_s: 2280,
      prep: { complete: true, active: null, steps: [] },
      runs: [
        {
          ...panelRun,
          started: true,
          complete: true,
          elapsed_s: 1980,
          cases_ready: Array.from({ length: 84 }, (_, i) => `${i}`),
        },
        {
          ...girderRun,
          started: true,
          complete: true,
          elapsed_s: 242,
          cases_ready: Array.from({ length: 84 }, (_, i) => `g${i}`),
        },
      ],
    },
  ],
];

process.stdout.write(`<!doctype html>
<html><head><meta charset="utf-8"><title>Capacity run status — preview</title>
<style>${css}</style>
<style>
  body { background:#111827; color:#e5e7eb; font-family:-apple-system,"Segoe UI",sans-serif; margin:0; padding:24px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:24px; max-width:1500px; }
  figcaption { font-size:11px; text-transform:uppercase; letter-spacing:.14em; color:#6b7280; margin:0 0 8px; }
  figure { margin:0; }
</style>
</head><body><div class="grid">
${states.map(([label, progress]) => panel(label, progress)).join("\n")}
</div></body></html>
`);
