import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {classifyFiles} from "@/components/storage/classifyFiles";
import {formatBytes, basenameOf, dirnameOf, shortSha} from "@/components/storage/storageHelpers";

const f = (name: string, lastModified = "2026-01-01T00:00:00Z") =>
    ({name, lastModified, size: 1} as never);

const sidecar = (ts: string) => ({git: {timestamp: ts}}) as never;

describe("classifyFiles", () => {
    test("ordinary keys stay in `regular`", () => {
        const {regular, branches} = classifyFiles([f("model.glb"), f("sub/dir/a.ifc")], new Map());
        assert.deepEqual(regular.map((x) => x.name), ["model.glb", "sub/dir/a.ifc"]);
        assert.deepEqual(branches, []);
    });

    test("a versions/<branch>/<sha>/<artefact> key becomes a branch tree", () => {
        const {regular, branches} = classifyFiles([f("versions/main/abc123/model.glb")], new Map());
        assert.deepEqual(regular, []);
        assert.equal(branches.length, 1);
        assert.equal(branches[0].encodedBranch, "main");
        assert.equal(branches[0].commits[0].sha, "abc123");
        assert.equal(branches[0].commits[0].leaves[0].artefactName, "model.glb");
    });

    test("a leading slash does not change the classification", () => {
        const {branches} = classifyFiles([f("/versions/main/abc/model.glb")], new Map());
        assert.equal(branches.length, 1);
    });

    test("`versions` with too few segments is an ordinary file, not a malformed branch", () => {
        const {regular, branches} = classifyFiles([f("versions/main/model.glb")], new Map());
        assert.equal(regular.length, 1);
        assert.deepEqual(branches, []);
    });

    test("build.json sidecars are hidden from the tree", () => {
        // They are metadata for the GLB, not separately loadable. A visible sidecar row
        // is a row that does nothing useful when clicked.
        const {branches} = classifyFiles(
            [f("versions/main/abc/model.glb"), f("versions/main/abc/model.build.json")],
            new Map(),
        );
        assert.equal(branches[0].commits[0].leaves.length, 1);
    });

    test("a branch with slashes round-trips through the __ encoding", () => {
        const {branches} = classifyFiles([f("versions/feat__thing/abc/m.glb")], new Map());
        assert.equal(branches[0].encodedBranch, "feat__thing");
        assert.equal(branches[0].displayBranch, "feat/thing");
    });

    test("commits sort newest-first, and the branch takes its newest commit's key", () => {
        const {branches} = classifyFiles(
            [
                f("versions/main/old/m.glb", "2026-01-01T00:00:00Z"),
                f("versions/main/new/m.glb", "2026-06-01T00:00:00Z"),
            ],
            new Map(),
        );
        assert.deepEqual(branches[0].commits.map((c) => c.sha), ["new", "old"]);
        assert.equal(branches[0].sortKey, branches[0].commits[0].sortKey);
    });

    test("the sidecar's git timestamp beats mtime, and says so", () => {
        // This is the rule worth protecting. Re-running CI on an OLD commit refreshes
        // its mtime, so mtime order is not commit order — and a wrong "latest" still
        // looks like a plausible commit, so nobody notices.
        const {branches} = classifyFiles(
            [
                f("versions/main/older-commit/m.glb", "2026-12-01T00:00:00Z"), // rebuilt recently
                f("versions/main/newer-commit/m.glb", "2026-02-01T00:00:00Z"),
            ],
            new Map([
                ["main/older-commit", sidecar("2026-01-01T00:00:00Z")],
                ["main/newer-commit", sidecar("2026-06-01T00:00:00Z")],
            ]),
        );
        assert.deepEqual(
            branches[0].commits.map((c) => c.sha),
            ["newer-commit", "older-commit"],
            "git timestamp must win over mtime",
        );
        assert.ok(branches[0].commits.every((c) => c.sortFromSidecar));
    });

    test("mtime is the fallback until the sidecar resolves", () => {
        const {branches} = classifyFiles([f("versions/main/abc/m.glb")], new Map());
        assert.equal(branches[0].commits[0].sortFromSidecar, false);
        assert.ok(branches[0].commits[0].sortKey > 0);
    });

    test("branches sort newest-first across branches", () => {
        const {branches} = classifyFiles(
            [
                f("versions/stale/a/m.glb", "2026-01-01T00:00:00Z"),
                f("versions/active/b/m.glb", "2026-09-01T00:00:00Z"),
            ],
            new Map(),
        );
        assert.deepEqual(branches.map((b) => b.encodedBranch), ["active", "stale"]);
    });
});

describe("storage helpers", () => {
    test("formatBytes switches unit at each 1024 boundary", () => {
        assert.equal(formatBytes(512), "512 B");
        assert.equal(formatBytes(1024), "1.0 KB");
        assert.equal(formatBytes(1024 * 1024), "1.0 MB");
        assert.equal(formatBytes(1024 ** 3), "1.00 GB");
    });

    test("dirname/basename handle a key with no slash", () => {
        assert.equal(dirnameOf("a.glb"), "");
        assert.equal(basenameOf("a.glb"), "a.glb");
        assert.equal(dirnameOf("x/y/a.glb"), "x/y");
        assert.equal(basenameOf("x/y/a.glb"), "a.glb");
    });

    test("shortSha truncates only when there is something to truncate", () => {
        assert.equal(shortSha("abc"), "abc");
        assert.equal(shortSha("0123456789abcdef"), "01234567");
    });
});
