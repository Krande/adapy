// The Convert page's and the progress toast's options: absent, each renders exactly as it did;
// present, each does its one thing.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: ConvertPage } = await import("@/components/convert/ConvertPage");
const { default: ConversionProgress } = await import("@/components/conversion/ConversionProgress");
const { isClaimedJob, jobSource, withoutClaimed } = await import("@/components/conversion/claimedLoads");
const { useLoadQueueStore } = await import("@/state/loadQueueStore");
const { useConversionStore } = await import("@/state/conversionStore");

type CpProps = import("@/components/conversion/ConversionProgress").ConversionProgressProps;

test("the convert page keeps its back link unless it is in a viewer", () => {
    const plain = renderToStaticMarkup(React.createElement(ConvertPage));
    assert.match(plain, /back to viewer/);
    assert.equal(renderToStaticMarkup(React.createElement(ConvertPage, { inViewer: false })), plain);
    const inViewer = renderToStaticMarkup(React.createElement(ConvertPage, { inViewer: true }));
    assert.doesNotMatch(inViewer, /back to viewer/);
    assert.equal(inViewer, plain.replace(/<a href="\/"[^>]*>[^<]*<\/a>/, ""));
});

setServerState(useLoadQueueStore, {
    current: { name: "current.ifc" } as never,
    queued: [{ name: "queued.ifc" } as never, { name: "other.ifc" } as never],
    errors: [{ name: "failed.ifc", message: "it broke" }],
});
setServerState(useConversionStore, {
    jobs: {
        "job.ifc::glb": { sourceKey: "job.ifc", status: "running", progress: 0.5 } as never,
        "bad.ifc::glb": { sourceKey: "bad.ifc", status: "error", error: "no reader" } as never,
    },
});

const toast = (props?: CpProps) => renderToStaticMarkup(React.createElement(ConversionProgress, props));

test("without a claim predicate the toast is unchanged", () => {
    const plain = toast();
    for (const name of ["current.ifc", "queued.ifc", "failed.ifc", "job.ifc", "bad.ifc"]) assert.match(plain, new RegExp(name));
    assert.equal(toast({}), plain);
    assert.equal(toast({ isClaimed: () => false }), plain);
});

test("claimed loads and their jobs leave the toast; the rest stay", () => {
    const claimed = new Set(["current.ifc", "queued.ifc", "failed.ifc", "job.ifc", "bad.ifc"]);
    const html = toast({ isClaimed: (name) => claimed.has(name) });
    for (const name of claimed) assert.doesNotMatch(html, new RegExp(name));
    assert.match(html, /other\.ifc/);
});

test("the claim rule", () => {
    assert.equal(jobSource("a.ifc::glb::s0.field"), "a.ifc");
    assert.equal(jobSource("plain"), "plain");
    const seen: [string, string | undefined][] = [];
    assert.equal(isClaimedJob("a.ifc::glb", (n, k) => (seen.push([n, k]), true)), true);
    assert.deepEqual(seen, [["a.ifc", "a.ifc::glb"]]);
    assert.equal(isClaimedJob("a.ifc::glb"), false);
    const load = { currentName: "a", queued: [{ name: "a" }, { name: "b" }], errors: [{ name: "b" }] };
    assert.deepEqual(withoutClaimed(load), { currentClaimed: false, queued: load.queued, errors: load.errors });
    assert.deepEqual(withoutClaimed(load, (n) => n === "a"), { currentClaimed: true, queued: [{ name: "b" }], errors: [{ name: "b" }] });
});
