import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {
    MENUS,
    menuCommandIds,
    resolveMenus,
    tidySeparators,
    type MenuDef,
    type ResolvedItem,
} from "@/shell/menuModel";
import type {Command} from "@/shell/commandFilter";

const cmd = (id: string, over: Partial<Command> = {}): Command => ({
    id,
    title: id,
    run: () => {},
    ...over,
});

describe("menu structure", () => {
    test("menu ids and labels are unique", () => {
        assert.equal(new Set(MENUS.map((m) => m.id)).size, MENUS.length);
        assert.equal(new Set(MENUS.map((m) => m.label)).size, MENUS.length);
    });

    test("no command is listed in two places", () => {
        const ids = menuCommandIds();
        const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
        assert.deepEqual(dupes, [], "a command in two menus means two addresses for one thing");
    });

    test("every menu names at least one command", () => {
        for (const m of MENUS) {
            assert.ok(menuCommandIds([m]).length > 0, `${m.label} names no commands`);
        }
    });

    test("command ids use a known namespace", () => {
        // Catches a typo like `panels:storage` or `actions:undo`, which would otherwise
        // just silently vanish from the menu.
        for (const id of menuCommandIds()) {
            assert.match(id, /^(action|panel|mode|layout|help|app):/, `${id} has no known prefix`);
        }
    });
});

describe("resolveMenus — groups", () => {
    const defs: MenuDef[] = [
        {id: "m", label: "M", items: [{kind: "group", prefix: "ws:"}]},
    ];

    test("expands to every command with the prefix, in registry order", () => {
        const menus = resolveMenus([cmd("ws:b"), cmd("other"), cmd("ws:a")], defs);
        const ids = menus[0].items.map((i) => (i.kind === "command" ? i.command.id : i.kind));
        assert.deepEqual(ids, ["ws:b", "ws:a"], "order follows the registry, not the alphabet");
    });

    test("a group matching nothing leaves no empty menu behind", () => {
        // Same rule as a submenu: a title you can click for nothing is worse than no
        // title. Before any workspace is saved, Window must not grow an empty section.
        assert.deepEqual(resolveMenus([cmd("other")], defs), []);
    });

    test("the prefix is a prefix, not a substring", () => {
        const menus = resolveMenus([cmd("x:ws:a")], defs);
        assert.deepEqual(menus, [], "matched a command whose id merely contains the prefix");
    });

    test("the Window menu actually carries the workspace group", () => {
        // The point of the kind. A typo in the prefix would silently list nothing, and
        // menuCommandIds cannot catch it — a group names no id.
        const json = JSON.stringify(MENUS);
        assert.ok(json.includes('"prefix":"layout:workspace:"'), "workspace group is missing");
    });
});

describe("resolveMenus", () => {
    test("drops items whose command does not exist", () => {
        const defs: MenuDef[] = [
            {id: "m", label: "M", items: [{kind: "command", id: "a"}, {kind: "command", id: "ghost"}]},
        ];
        const [m] = resolveMenus([cmd("a")], defs);
        assert.equal(m.items.length, 1);
    });

    test("drops a menu left empty", () => {
        const defs: MenuDef[] = [{id: "m", label: "M", items: [{kind: "command", id: "ghost"}]}];
        assert.deepEqual(resolveMenus([], defs), [], "a title you can click for nothing is worse than no title");
    });

    test("keeps disabled commands — they are greyed, not hidden", () => {
        const defs: MenuDef[] = [{id: "m", label: "M", items: [{kind: "command", id: "a"}]}];
        const [m] = resolveMenus([cmd("a", {enabled: false, disabledReason: "nope"})], defs);
        assert.equal(m.items.length, 1);
        const it = m.items[0];
        assert.equal(it.kind, "command");
        if (it.kind === "command") assert.equal(it.command.enabled, false);
    });

    test("a submenu with nothing left in it disappears", () => {
        const defs: MenuDef[] = [
            {
                id: "m",
                label: "M",
                items: [
                    {kind: "command", id: "a"},
                    {kind: "submenu", label: "Sub", items: [{kind: "command", id: "ghost"}]},
                ],
            },
        ];
        const [m] = resolveMenus([cmd("a")], defs);
        assert.equal(m.items.length, 1);
    });
});

describe("tidySeparators", () => {
    const s: ResolvedItem = {kind: "separator"};
    const c = (id: string): ResolvedItem => ({kind: "command", command: cmd(id)});

    test("drops a leading separator", () => {
        assert.deepEqual(tidySeparators([s, c("a")]).length, 1);
    });

    test("drops a trailing separator", () => {
        assert.deepEqual(tidySeparators([c("a"), s]).length, 1);
    });

    test("collapses runs of separators", () => {
        // This is the real case: `mode:inspect` is absent while you are in Inspect, and a
        // non-REST deployment has no admin panel, so whole groups vanish and leave their
        // rules behind. Two rules with nothing between them reads as a rendering bug.
        const out = tidySeparators([c("a"), s, s, s, c("b")]);
        assert.equal(out.length, 3);
        assert.equal(out[1].kind, "separator");
    });

    test("a list of only separators comes back empty", () => {
        assert.deepEqual(tidySeparators([s, s]), []);
    });

    test("leaves an already-tidy list alone", () => {
        const input = [c("a"), s, c("b")];
        assert.deepEqual(tidySeparators(input), input);
    });
});
