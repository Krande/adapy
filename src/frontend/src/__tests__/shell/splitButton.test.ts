import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {chosenTypeLabel, splitButtonState} from "@/shell/splitButton";

const types = [
    {slug: "door", name: "Door", origin: "db"},
    {slug: "hatch", name: "Hatch", origin: "code"},
];
const label = (t: {name: string}) => t.name;

describe("chosenTypeLabel", () => {
    test("names the chosen type", () => {
        assert.equal(chosenTypeLabel(types, "hatch", label), "Hatch");
    });

    test("nothing chosen is null, not a guess at the first type", () => {
        assert.equal(chosenTypeLabel(types, null, label), null);
    });

    test("a stale slug reads as nothing chosen", () => {
        // A model can be reloaded against a different catalogue while the old slug sits
        // in the store. Naming a type that no longer exists is worse than admitting none
        // is chosen — the button would claim it places a Door and then place nothing.
        assert.equal(chosenTypeLabel(types, "porthole", label), null);
    });
});

describe("splitButtonState", () => {
    const base = {label: "Add opening", hasMenu: true, chosen: null as string | null, pressed: false};

    test("a chosen type fires straight away", () => {
        // The whole point: placing the tenth identical door must not cost a menu.
        const s = splitButtonState({...base, chosen: "Door (db)"});
        assert.equal(s.action, "run");
        assert.equal(s.tooltip, "Add opening: Door (db)");
    });

    test("nothing chosen opens the picker instead of arming", () => {
        // Arming to place "nothing" is a press with no visible effect, which reads as a
        // button that does not work.
        const s = splitButtonState(base);
        assert.equal(s.action, "pick");
        assert.equal(s.tooltip, "Add opening — choose a type");
    });

    test("an armed tool always fires, so a second press can disarm it", () => {
        // Even with nothing chosen: offering a type picker to cancel something is
        // answering a question nobody asked.
        assert.equal(splitButtonState({...base, pressed: true}).action, "run");
        assert.equal(splitButtonState({...base, pressed: true, chosen: "Door"}).action, "run");
    });

    test("the picker's noun follows what it picks", () => {
        // Export picks a format, not a type. Hardcoding "type" made the export button
        // say "choose a type", which is the kind of wrong word that makes a control read
        // as somebody else's, pasted in.
        assert.equal(
            splitButtonState({...base, label: "Export", noun: "format"}).tooltip,
            "Export — choose a format",
        );
    });

    test("a tool with no menu is an ordinary button", () => {
        const s = splitButtonState({label: "Compile preview", hasMenu: false, chosen: null, pressed: false});
        assert.equal(s.action, "run");
        assert.equal(s.tooltip, "Compile preview");
    });

    test("the tooltip never claims a type that is not chosen", () => {
        for (const pressed of [true, false]) {
            const s = splitButtonState({...base, pressed});
            assert.ok(!s.tooltip.includes(":"), `leaked a type in ${s.tooltip}`);
        }
    });
});
