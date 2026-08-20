import {test, describe} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// Every viewport overlay must be mounted by something in src/shell.
//
// Five times during this rewrite a feature was lost because the classic UI rendered it
// and the shell did not: the plugin top-bar regions, the legacy visibility flags,
// AuthGate, useUrlParamLoad + RestModeUI, and — after Menu.tsx was deleted — the
// cellbuilder's context menu, port menu, insert menu and gizmo HUD.
//
// The failure is always silent. Nothing throws: the controller keeps opening a menu into
// a store nobody renders, and the only symptom is that right-click stops doing anything.
// A mounted-but-unreferenced child leaves no other trace, which is why the deletion that
// caused it looked safe.
//
// This is a source-text test, so it asserts its anchors exist before asserting anything
// about them — a lesson from regionCompat, which silently degraded into a test of nothing
// when the string it sliced on disappeared.

const SHELL_DIR = path.resolve(import.meta.dirname, "../../shell");

/** Components that must be rendered somewhere under src/shell to be reachable at all. */
const MUST_BE_MOUNTED = [
    "CellBuilderContextMenu",
    "CellBuilderPortMenu",
    "CellBuilderInsertMenu",
    "CellBuilderGizmoHud",
    "ColorLegend",
    "RestModeUI",
    "ConfirmHost",
    "HelpDialogs",
    "MarkingMenu",
];

function shellSource(): string {
    const files = fs
        .readdirSync(SHELL_DIR)
        .filter((f) => /\.tsx?$/.test(f))
        .map((f) => fs.readFileSync(path.join(SHELL_DIR, f), "utf8"));
    assert.ok(files.length > 5, "could not read the shell sources — re-anchor this test");
    return files.join("\n");
}

describe("viewport overlays are mounted by the shell", () => {
    const src = shellSource();

    for (const name of MUST_BE_MOUNTED) {
        test(`${name} is rendered somewhere in src/shell`, () => {
            // Imported AND rendered. An import alone is what a dead reference looks like.
            assert.match(src, new RegExp(`\\b${name}\\b`), `${name} is not referenced in src/shell`);
            assert.match(
                src,
                new RegExp(`<${name}[\\s/>]`),
                `${name} is imported but never rendered — that is exactly how the ` +
                    `cellbuilder menus disappeared when Menu.tsx was deleted`,
            );
        });
    }

    test("useUrlParamLoad is called, not merely imported", () => {
        // ?file= / ?scope= deep links. A hook that is imported and not called is the same
        // silent failure in a different shape.
        assert.match(src, /useUrlParamLoad\(\)/, "the deep-link loader is not invoked");
    });
});
