import assert from "node:assert/strict";
import {test} from "node:test";

import {filterCommands, scoreCommand, type Command} from "../../shell/commandFilter";

// The palette's ranking. Worth testing on its own because it is the part users feel:
// typing three letters and getting the wrong first result is the difference between a
// palette that replaces the menus and one nobody uses twice.

const cmd = (title: string, over: Partial<Command> = {}): Command => ({
    id: title.toLowerCase().replace(/\s+/g, "-"),
    title,
    run: () => {},
    ...over,
});

test("an empty query keeps every command, in registry order", () => {
    const all = [cmd("Fit all to view"), cmd("Undo"), cmd("Upload files")];
    assert.deepEqual(filterCommands(all, "").map((c) => c.title), all.map((c) => c.title));
});

test("a title prefix outranks a mid-word hit", () => {
    const results = filterCommands([cmd("Toggle the colour legend"), cmd("Legend settings")], "legend");
    assert.equal(results[0].title, "Legend settings", "the command that STARTS with the query wins");
});

test("a word-start hit outranks a mid-word one", () => {
    const results = filterCommands([cmd("Unlegendary"), cmd("Toggle the legend")], "legend");
    assert.equal(results[0].title, "Toggle the legend");
});

test("titles outrank keywords", () => {
    // Otherwise a command that merely mentions a synonym buries the one actually named
    // for what you typed.
    const results = filterCommands(
        [cmd("Convert files", {keywords: "upload import"}), cmd("Upload files")],
        "upload",
    );
    assert.equal(results[0].title, "Upload files");
});

test("initialisms match as a subsequence", () => {
    // "fta" -> "Fit all to view". Cheap tolerance that makes the palette usable without
    // knowing the exact wording.
    assert.notEqual(scoreCommand(cmd("Fit all to view"), "fta"), null);
    assert.notEqual(scoreCommand(cmd("Toggle the colour legend"), "tcl"), null);
});

test("a query with no match at all is excluded", () => {
    assert.equal(scoreCommand(cmd("Fit all to view"), "zzzz"), null);
    assert.deepEqual(filterCommands([cmd("Fit all to view")], "zzzz"), []);
});

test("matching is case-insensitive and ignores surrounding space", () => {
    assert.notEqual(scoreCommand(cmd("Fit all to view"), "  FIT  "), null);
});

test("keywords make a command findable under a name it does not carry", () => {
    // "isolate" is what the user calls it; "Hide selection" is what it is called.
    const c = cmd("Hide selection", {keywords: "isolate"});
    assert.notEqual(scoreCommand(c, "isolate"), null);
});
