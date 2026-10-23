import {describe, it} from "node:test";
import assert from "node:assert/strict";
import {compareProjects, sortProjects, sortProjectsAmong, sortProjectsBy} from "../../utils/sortProjects";

describe("sortProjects", () => {
    it("sorts alphabetically, ignoring case", () => {
        assert.deepEqual(sortProjects(["ccc", "AAA", "bbb", "Aab"]), ["AAA", "Aab", "bbb", "ccc"]);
    });

    it("compares digit runs as numbers", () => {
        assert.deepEqual(sortProjects(["P10", "P2", "P1", "p3"]), ["P1", "P2", "p3", "P10"]);
    });

    it("returns a new array and leaves the input alone", () => {
        const given = ["BBB", "AAA"];
        const out = sortProjects(given);
        assert.deepEqual(out, ["AAA", "BBB"]);
        assert.deepEqual(given, ["BBB", "AAA"]);
        assert.notEqual(out, given);
    });

    it("orders names equal but for case the same way whatever the input order", () => {
        assert.deepEqual(sortProjects(["aaa", "AAA"]), sortProjects(["AAA", "aaa"]));
        assert.equal(compareProjects("AAA", "aaa"), -compareProjects("aaa", "AAA"));
        assert.notEqual(compareProjects("AAA", "aaa"), 0);
    });

    it("does not take the runtime's locale tailoring (a Nordic 'AA' is not sorted as 'Å')", () => {
        assert.deepEqual(sortProjects(["ZZZ", "AAB", "BBB"]), ["AAB", "BBB", "ZZZ"]);
    });

    it("handles an empty list", () => {
        assert.deepEqual(sortProjects([]), []);
    });
});

describe("sortProjectsBy", () => {
    it("sorts records by the name the key picks out", () => {
        const rows = [{name: "ccc", id: 1}, {name: "AAA", id: 2}, {name: "bbb", id: 3}];
        assert.deepEqual(
            sortProjectsBy(rows, (r) => r.name).map((r) => r.id),
            [2, 3, 1],
        );
    });
});

describe("sortProjectsAmong", () => {
    it("sorts the projects among their own slots and keeps fixed entries in place", () => {
        const scopes = [
            {kind: "user", name: "Personal"},
            {kind: "shared", name: "Shared"},
            {kind: "project", name: "CCC"},
            {kind: "project", name: "aaa"},
            {kind: "project", name: "BBB"},
            {kind: "corpus", name: "Corpus"},
        ];
        const out = sortProjectsAmong(scopes, (s) => s.kind === "project", (s) => s.name);
        assert.deepEqual(
            out.map((s) => s.name),
            ["Personal", "Shared", "aaa", "BBB", "CCC", "Corpus"],
        );
        assert.equal(scopes[2].name, "CCC", "the input is not touched");
    });
});
