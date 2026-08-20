import {test, describe} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// No panel decides its own visibility.
//
// The dock decides. A panel that also gates itself on a store boolean can be docked,
// mounted, and still render nothing — and the reason is invisible, because from the
// outside it looks exactly like a panel that crashed. That was `useLegacyFlagSync`'s
// whole reason to exist: it kept the old flags in step with the dock so the panels would
// agree to draw.
//
// A source-text check rather than a render test, because the failure it guards is a line
// someone adds back — `if (!visible) return null` at the top of a panel body — and no
// render test would catch it without also arranging the exact state that hides it.

const ROOT = path.resolve(import.meta.dirname, "../..");

/** Store booleans that used to mean "this panel is on screen". */
const VISIBILITY_FLAGS = ["panelVisible", "isPanelOpen", "isControlsVisible", "showServerInfoBox"];

const PANELS = [
    "components/viewer/CellBuilderPanel.tsx",
    "components/simulation/SimulationDataInfoPanel.tsx",
    "components/info_box_scene/ScenePanel.tsx",
    "components/properties/PropertiesPanel.tsx",
];

const read = (rel: string) => {
    const p = path.join(ROOT, rel);
    return fs.existsSync(p) ? fs.readFileSync(p, "utf8") : null;
};

describe("panels do not gate their own visibility", () => {
    for (const rel of PANELS) {
        test(`${path.basename(rel)} has no early return on a visibility flag`, () => {
            const src = read(rel);
            if (src === null) return; // renamed or removed; panelRegistry.test owns that
            for (const flag of VISIBILITY_FLAGS) {
                // `if (!s.panelVisible) return null` and friends, on one line.
                const gate = new RegExp(`if\\s*\\([^)]*!\\s*[\\w.]*\\b${flag}\\b[^)]*\\)\\s*return`);
                assert.ok(
                    !gate.test(src),
                    `${rel} returns early on ${flag} — the dock owns visibility, and a ` +
                        `docked panel that refuses to draw looks identical to a broken one`,
                );
            }
        });
    }

    test("the layout→flag bridge is gone", () => {
        // It was always meant to come out at the cutover. Its replacement runs the other
        // way: business logic asks for a panel, the shell opens it.
        assert.equal(read("shell/useLegacyFlagSync.ts"), null, "useLegacyFlagSync is back");
        assert.ok(read("shell/usePanelReveal.ts"), "usePanelReveal is missing");
    });

    test("the reveal watcher is mounted", () => {
        // Fifth instance of this class of bug in the rebuild: bootstrap work living in a
        // module nothing renders. Always silent, never a type error.
        const shell = read("shell/AppShell.tsx") ?? "";
        assert.match(shell, /usePanelReveal\(\)/, "AppShell never calls usePanelReveal");
    });

    test("reveal is rising-edge only", () => {
        // Acting on false would close the Builder out from under you when the model
        // closes, instead of letting it show its own empty state.
        const src = read("shell/usePanelReveal.ts") ?? "";
        assert.match(src, /now\s*&&\s*!prev/, "usePanelReveal no longer tests a rising edge");
    });
});
