#!/usr/bin/env node
// Reports every ad-hoc `className` string in the viewer UI, bucketed by shape, so the
// design-system migration has a burn-down metric instead of a vibe. Read-only: it
// never edits a file.
//
// Run: `node scripts/ui-audit.mjs` (also wired as `npm run ui:audit`).
//   --csv <path>   where to write the row-level report (default docs/ui-audit.csv)
//   --json <path>  optional machine-readable summary, for diffing between milestones
//   --top <n>      how many recipes to print (default 25)
//
// The counts this prints are the numbers quoted in docs/FEATURE_INVENTORY.md and in
// the milestone notes; re-run it after each milestone and the totals should fall.

import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { dirname, resolve, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");
const srcDir = resolve(root, "src");

const args = process.argv.slice(2);
const argOf = (flag, fallback) => {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
};
const csvPath = resolve(root, argOf("--csv", "docs/ui-audit.csv"));
const jsonPath = argOf("--json", null);
const topN = Number(argOf("--top", "25"));

// Generated flatbuffer bindings and the design system itself are not migration targets:
// the former is machine-written, the latter is where the recipes are *supposed* to live.
const SKIP_DIRS = new Set(["flatbuffers", "node_modules", "__tests__"]);
const SKIP_PATH_FRAGMENTS = ["components" + sep + "ui" + sep, "components" + sep + "icons" + sep];

/** Every .tsx/.ts under src/, minus the generated and exempt trees. */
function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = resolve(dir, entry);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(entry)) walk(full, out);
    } else if (/\.tsx?$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

// A Tailwind-ish token: utility prefixes, arbitrary values, variants. Used both to decide
// whether a bare string constant is really a class recipe and to normalise for bucketing.
const TW_TOKEN =
  /^(?:[a-z-]+:)*(?:-?(?:bg|text|border|rounded|p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|w|h|min|max|flex|grid|gap|items|justify|self|absolute|relative|fixed|sticky|top|bottom|left|right|z|opacity|shadow|ring|outline|cursor|overflow|font|leading|tracking|space|divide|transition|duration|ease|animate|hover|focus|active|disabled|group|peer|inline|block|hidden|truncate|whitespace|select|pointer|inset|order|col|row|aspect|object|fill|stroke|backdrop|filter|blur|scale|rotate|translate|origin|resize|list|align|table|sr)\b|\[)/;

const isClassish = (s) => {
  const parts = s.trim().split(/\s+/).filter(Boolean);
  if (parts.length < 2) return false;
  const hits = parts.filter((p) => TW_TOKEN.test(p)).length;
  return hits >= 2 && hits / parts.length > 0.5;
};

/**
 * Bucket key for "the same recipe written twice". Interpolations collapse to ${}, class
 * order is ignored (Tailwind is order-independent), whitespace normalised. Two call sites
 * that differ only in a conditional colour land in the same bucket, which is what we want:
 * they are one primitive with one variant prop.
 */
const normalise = (s) =>
  s
    .replace(/\$\{[^}]*\}/g, "${}")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .sort()
    .join(" ");

/** Which primitive should own this recipe, judged from the element it is attached to. */
function suggestPrimitive(element, cls) {
  switch (element) {
    case "button":
      return /\bw-\d|\bh-\d|aspect-square/.test(cls) && !/px-|py-/.test(cls)
        ? "IconButton"
        : "Button";
    case "select":
      return "Select";
    case "textarea":
      return "Textarea";
    case "input":
      if (/type=["']checkbox/.test(cls)) return "Checkbox";
      if (/type=["']radio/.test(cls)) return "RadioGroup";
      if (/type=["']range/.test(cls)) return "Slider";
      if (/type=["']number/.test(cls)) return "NumberField";
      return "Input";
    case "table":
    case "tbody":
    case "tr":
    case "td":
    case "th":
      return "DataTable";
    case "label":
      return "Field";
    default:
      break;
  }
  if (/\bfixed\b.*\binset-0\b|\bz-\[?[5-9]\d/.test(cls)) return "Dialog";
  if (/\brounded\b.*\bborder\b.*\bp-\d/.test(cls)) return "Panel";
  if (/\bflex\b.*\bgap-\d/.test(cls) && /\bitems-center\b/.test(cls)) return "Toolbar";
  return "";
}

const files = walk(srcDir);
const rows = [];
const buckets = new Map(); // normalised recipe -> {count, files:Set, sample, primitive}
const perFile = new Map();

// Raw-element census: how many un-primitived controls exist at all. This is the
// denominator the migration is measured against.
const elementCounts = { button: 0, input: 0, select: 0, textarea: 0, checkbox: 0 };
const hardcodedColourFiles = new Set();

// className={...} in all three spellings, plus bare class-recipe constants.
const CLASS_ATTR = /className\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*`([^`]*)`\s*\}|\{\s*"([^"]*)"\s*\}|\{\s*'([^']*)'\s*\})/g;
const CONST_RECIPE = /\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|`([^`]*)`)\s*[;\n]/g;
const HARDCODED_COLOUR = /\bbg-(?:gray|blue|red|green|yellow|indigo|slate|zinc|neutral|stone)-\d{2,3}\b/;

for (const file of files) {
  const rel = relative(root, file).split(sep).join("/");
  if (SKIP_PATH_FRAGMENTS.some((f) => file.includes(f))) continue;

  const text = readFileSync(file, "utf8");
  const lines = text.split(/\r?\n/);

  for (const m of text.matchAll(/<(button|input|select|textarea)\b[^>]*>/g)) {
    const tag = m[1];
    if (tag === "input" && /type\s*=\s*["']checkbox["']/.test(m[0])) elementCounts.checkbox++;
    else elementCounts[tag]++;
  }

  const record = (raw, index, element) => {
    const cls = raw.trim();
    if (!cls || !isClassish(cls)) return;
    const line = text.slice(0, index).split(/\r?\n/).length;
    const key = normalise(cls);
    const primitive = suggestPrimitive(element, cls);

    rows.push({ file: rel, line, cls, primitive });
    perFile.set(rel, (perFile.get(rel) ?? 0) + 1);

    const b = buckets.get(key) ?? { count: 0, files: new Set(), sample: cls, primitive };
    b.count++;
    b.files.add(rel);
    if (primitive && !b.primitive) b.primitive = primitive;
    buckets.set(key, b);
  };

  for (const m of text.matchAll(CLASS_ATTR)) {
    const cls = m[1] ?? m[2] ?? m[3] ?? m[4] ?? m[5] ?? "";
    // Look back for the tag this className belongs to, so the suggestion is element-aware.
    const before = text.slice(Math.max(0, m.index - 400), m.index);
    const tagMatch = [...before.matchAll(/<([a-zA-Z][\w.]*)\b/g)].pop();
    record(cls, m.index, tagMatch ? tagMatch[1].toLowerCase() : "");
  }

  for (const m of text.matchAll(CONST_RECIPE)) {
    record(m[2] ?? m[3] ?? m[4] ?? "", m.index, "");
  }

  if (lines.some((l) => HARDCODED_COLOUR.test(l))) hardcodedColourFiles.add(rel);
}

mkdirSync(dirname(csvPath), { recursive: true });
const csvCell = (v) => `"${String(v).replace(/"/g, '""')}"`;
writeFileSync(
  csvPath,
  ["file,line,classString,suggestedPrimitive"]
    .concat(rows.map((r) => [r.file, r.line, r.cls, r.primitive].map(csvCell).join(",")))
    .join("\n") + "\n",
  "utf8",
);

const ranked = [...buckets.entries()].sort((a, b) => b[1].count - a[1].count);
// Deliberately no timestamp: this summary is committed and diffed between milestones,
// so a re-run with no code change must produce a byte-identical file. Git dates it.
const summary = {
  filesScanned: files.length,
  classStrings: rows.length,
  distinctRecipes: buckets.size,
  filesWithHardcodedColour: hardcodedColourFiles.size,
  rawElements: elementCounts,
};

if (jsonPath) {
  const out = resolve(root, jsonPath);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, JSON.stringify({ ...summary, topRecipes: ranked.slice(0, 100).map(([k, v]) => ({ recipe: k, count: v.count, files: v.files.size, primitive: v.primitive })) }, null, 2) + "\n", "utf8");
}

const pad = (s, n) => String(s).padEnd(n);
console.log(`\nUI audit — ${summary.filesScanned} files scanned\n`);
console.log(`  class strings          ${summary.classStrings}`);
console.log(`  distinct recipes       ${summary.distinctRecipes}`);
console.log(`  files w/ hardcoded bg  ${summary.filesWithHardcodedColour}`);
console.log(`  raw <button>           ${elementCounts.button}`);
console.log(`  raw <input>            ${elementCounts.input}`);
console.log(`  raw checkboxes         ${elementCounts.checkbox}`);
console.log(`  raw <select>           ${elementCounts.select}`);
console.log(`  raw <textarea>         ${elementCounts.textarea}`);

console.log(`\nTop ${topN} recipes by call sites (these are the codemod targets):\n`);
console.log(`  ${pad("uses", 6)}${pad("files", 7)}${pad("primitive", 15)}recipe`);
for (const [, v] of ranked.slice(0, topN)) {
  const sample = v.sample.replace(/\s+/g, " ").slice(0, 78);
  console.log(`  ${pad(v.count, 6)}${pad(v.files.size, 7)}${pad(v.primitive || "—", 15)}${sample}`);
}

const worst = [...perFile.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
console.log(`\nHeaviest files:\n`);
for (const [f, n] of worst) console.log(`  ${pad(n, 6)}${f}`);

console.log(`\nRow-level report: ${relative(root, csvPath).split(sep).join("/")}\n`);
