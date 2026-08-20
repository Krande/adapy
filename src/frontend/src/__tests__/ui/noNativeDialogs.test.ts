import {test, describe} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// Native window.confirm / prompt / alert are banned outside the allowlist.
//
// They are blocking, unstyleable, and visibly not part of the application: a browser
// dialog over a dark themed viewer reads as a different program. In the embed build it
// is worse — the dialog carries the HOST page's origin, so a docs page shows
// "docs.example.com says: Delete file?", which looks like a phishing attempt.
//
// `confirm()` / `promptText()` / `alertText()` from @/ui/confirm are the replacements;
// they render through ConfirmHost and are awaited exactly the same way.
//
// The allowlist is a burn-down, same as noAdHocChrome's. It shrinks; it never grows.

const ROOT = path.resolve(import.meta.dirname, "../..");

// The 14 admin tabs are scheduled last (milestone M7) and are wrapped rather than
// rewritten. Every entry here is a file still to convert.
const ALLOW = new Set([
    "components/admin/AuditLogTab.tsx",
    "components/admin/AuditRunsTab.tsx",
    "components/admin/CorpusTab.tsx",
    "components/admin/FileTreeView.tsx",
    "components/admin/SchedulesTab.tsx",
    "components/admin/StorageTab.tsx",
    "components/admin/WorkersTab.tsx",
]);

function walk(dir: string, out: string[] = []): string[] {
    for (const e of fs.readdirSync(dir, {withFileTypes: true})) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) {
            if (e.name === "__tests__" || e.name === "node_modules") continue;
            walk(full, out);
        } else if (/\.tsx?$/.test(e.name)) {
            out.push(full);
        }
    }
    return out;
}

/** Calls only — a comment saying "replaces the old window.prompt flow" is not a call. */
const CALL = /(?<![\w.])window\.(confirm|prompt|alert)\s*\(/;

function offenders(): string[] {
    const bad: string[] = [];
    for (const file of walk(path.join(ROOT, "components")).concat(walk(path.join(ROOT, "shell")))) {
        const rel = path.relative(ROOT, file).split(path.sep).join("/");
        const lines = fs.readFileSync(file, "utf8").split("\n");
        const hit = lines.some((l) => !l.trimStart().startsWith("//") && !l.trimStart().startsWith("*") && CALL.test(l));
        if (hit) bad.push(rel);
    }
    return bad;
}

describe("no native browser dialogs", () => {
    test("only allowlisted files still call window.confirm/prompt/alert", () => {
        const bad = offenders().filter((f) => !ALLOW.has(f));
        assert.deepEqual(bad, [], `use @/ui/confirm instead of a native dialog in:\n  ${bad.join("\n  ")}`);
    });

    test("the allowlist has no stale entries", () => {
        // A burn-down list that keeps names of already-converted files stops being a
        // measure of anything, and quietly re-permits what it names.
        const current = new Set(offenders());
        const stale = [...ALLOW].filter((f) => !current.has(f));
        assert.deepEqual(stale, [], `already converted — drop from the allowlist:\n  ${stale.join("\n  ")}`);
    });

    test("StorageBrowser is converted and stays converted", () => {
        // Named explicitly because it held nine of them and is the panel the user meets
        // first: delete, rename, upload failures and the template prompt all went
        // through native dialogs.
        assert.ok(!ALLOW.has("components/storage/StorageBrowser.tsx"));
        assert.ok(!offenders().includes("components/storage/StorageBrowser.tsx"));
    });
});
