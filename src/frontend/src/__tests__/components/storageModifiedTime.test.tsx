import assert from "node:assert/strict";
import {test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

// `runtime/config` reads `window` at module scope, and FileRow reaches it
// through `canOpenInScene`. A static import would evaluate that before this
// line runs, so the shim goes first and the component is imported after it --
// which is why the imports below are dynamic and the ones above are not.
(globalThis as unknown as {window: unknown}).window = globalThis;

const {default: FileRow} = await import("@/components/storage/browser/FileRow");
const {formatRelative, parseLastModifiedMs} = await import("@/components/storage/browser/helpers");
const {localDateTime} = await import("@/utils/time");
type ServerFileEntry = import("@/state/serverInfoStore").ServerFileEntry;

// When a storage row shows its last-modified time, and what it shows.
//
// THE QUESTION THIS ANSWERS. A scope accumulates `review/x.glb` beside
// `review/x-v9.glb` and nothing in the row says which one is current. The time
// was rendered only in the MAXIMIZED panel, so answering it meant opening a
// second view -- or guessing.

const file = (over: Partial<ServerFileEntry> = {}): ServerFileEntry =>
    ({
        name: "review/ap400-stru-ms-ours.glb",
        size: 3622692,
        lastModified: "2026-09-17T10:49:00Z",
        ...over,
    }) as ServerFileEntry;

const render = (over: Partial<React.ComponentProps<typeof FileRow>> = {}) =>
    renderToStaticMarkup(
        <FileRow
            file={file()}
            displayName="ap400-stru-ms-ours.glb"
            indentLevel={0}
            viewingName={null}
            loadedSourceNames={new Set<string>()}
            conversionJobs={{}}
            expandedName={null}
            setExpandedName={() => {}}
            onToggle={async () => {}}
            setPickerName={() => {}}
            isSelected={false}
            onSelectToggle={() => {}}
            menuItems={[]}
            showModified
            {...over}
        />,
    );

test("a row shows its last-modified time without being maximized", () => {
    const html = render();
    assert.ok(html.includes(formatRelative(file().lastModified)));
});

test("the tooltip is the absolute local time, not the relative one", () => {
    // "2 d ago" answers which of two files is newer. It does not answer
    // whether this is the copy uploaded at 10:49, and that is the next
    // question every time.
    const html = render();
    assert.ok(html.includes(localDateTime("2026-09-17T10:49:00Z")));
    assert.ok(!html.includes('title="2026-09-17T10:49:00Z"'), "not the raw UTC wire value");
});

test("a row with no timestamp renders no cell at all", () => {
    // An empty span still takes its gap and reads as a value that failed to
    // load, which is worse than saying nothing.
    const html = render({file: file({lastModified: ""})});
    assert.ok(!html.includes("tabular-nums"));
});

test("an unparseable timestamp is treated as absent rather than echoed", () => {
    assert.equal(parseLastModifiedMs("not a date"), 0);
    const html = render({file: file({lastModified: "not a date"})});
    assert.ok(!html.includes("not a date"));
});

test("opting out still works, for a list whose header already carries the time", () => {
    const html = render({showModified: false});
    assert.ok(!html.includes("tabular-nums"));
});

// The wording itself, because it is what someone reads to decide.
test("recent times read as an elapsed interval and old ones as a date", () => {
    const now = Date.now();
    assert.equal(formatRelative(new Date(now - 30_000).toISOString()), "just now");
    assert.equal(formatRelative(new Date(now - 90 * 60_000).toISOString()), "2 h ago");
    assert.equal(formatRelative(new Date(now - 3 * 86_400_000).toISOString()), "3 d ago");
    // Past a week the interval stops being useful and a date takes over.
    const old = new Date(now - 60 * 86_400_000).toISOString();
    assert.match(formatRelative(old), /^\d{4}-\d{2}-\d{2}$/);
});
