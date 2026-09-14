import assert from "node:assert/strict";
import {test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

// The member table's actions column used to be `w-24` (96px) while a `ci` row
// renders three buttons -- rotate/revoke/remove -- that together need ~165px
// including the flex gaps and the cell padding. The table is `table-fixed` and
// the table-level cell class carries `truncate` (overflow:hidden), so the
// surplus was not scrolled and not wrapped, it was CLIPPED: "remove" was
// invisible and unclickable on exactly the rows that have all three buttons.
//
// The harness has no layout engine, so the pixel arithmetic itself cannot be
// asserted here -- that was measured in a browser. What IS worth pinning is the
// pair of decisions that arithmetic produced, because either one silently
// reintroduces the bug: the column must stay wide enough to have been chosen
// deliberately, and the cell must NOT inherit a truncating class, so that a
// future fourth button spills visibly instead of disappearing.
//
// `ProjectsTab` pulls in `viewerApi`, which touches browser storage at module
// scope, so the globals it reads are stubbed before the dynamic import -- the
// arrangement workerPackagesMissing.test and oidcScopedToken.test use.

function fakeStorage(): Storage {
    const m = new Map<string, string>();
    return {
        getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
        setItem: (k: string, v: string) => void m.set(k, String(v)),
        removeItem: (k: string) => void m.delete(k),
        clear: () => m.clear(),
        key: (i: number) => [...m.keys()][i] ?? null,
        get length() {
            return m.size;
        },
    } as Storage;
}

const g = globalThis as unknown as Record<string, unknown>;
g.sessionStorage = fakeStorage();
g.localStorage = fakeStorage();
g.window = {location: {origin: "https://app.example.test", pathname: "/", search: ""}};

const {memberColumns} = await import("../../components/admin/ProjectsTab");

const member = (over: Partial<{user_sub: string; role: string}> = {}) => ({
    user_sub: "sub-1",
    role: "member",
    added_at: null,
    email: null,
    display_name: null,
    last_seen_at: null,
    ...over,
});

const handlers = (archived = false) => ({
    archived,
    ciBotBusy: false,
    onRotateCiBot: async () => {},
    onRevokeCiBot: async () => {},
    onRemove: () => {},
});

const actionsColumn = (archived = false) => {
    const col = memberColumns(handlers(archived)).find((c) => c.key === "actions");
    assert.ok(col, "the member table still has an actions column");
    return col;
};

const cellHtml = (archived: boolean, role: string) =>
    renderToStaticMarkup(
        <>{actionsColumn(archived).cell(member({role, user_sub: `ci:demo`}), 0)}</>,
    );

test("a ci row renders all three actions, a plain member only remove", () => {
    const ci = cellHtml(false, "ci");
    assert.equal((ci.match(/<button/g) ?? []).length, 3);
    for (const label of ["rotate", "revoke", "remove"]) assert.ok(ci.includes(`>${label}<`));

    const plain = cellHtml(false, "member");
    assert.equal((plain.match(/<button/g) ?? []).length, 1);
    assert.ok(plain.includes(">remove<"));
});

test("the actions column is wide enough for the three-button row", () => {
    // 12rem = 192px against ~165px of content. Anything below ~11rem clips the
    // last button; the assertion is on the declared rem so a future edit that
    // shrinks the column has to come past this test and re-do the arithmetic.
    const width = actionsColumn().col?.className;
    assert.equal(width, "w-48");
});

test("the actions cell opts out of the table's truncate", () => {
    // The table passes `px-3 py-1 truncate` as the default cell class. Text
    // columns want it; a cell of controls does not -- overflow:hidden makes an
    // overflowing button unreachable rather than merely ugly.
    const cls = actionsColumn().cellClassName;
    assert.equal(typeof cls, "string");
    assert.ok(!(cls as string).includes("truncate"));
    assert.ok((cls as string).includes("whitespace-nowrap"));
});

test("an archived project renders no actions at all", () => {
    assert.equal(cellHtml(true, "ci"), "");
});
