// The console of a run: its activity feed read as it grows (services/simulations/activityFeed.ts),
// the store that follows the feed of the run on screen (state/runConsoleStore.ts), and what the
// console shows of it at a moment (components/simulations/RunConsole.tsx).
import {test} from "node:test";
import assert from "node:assert/strict";

import {consoleAt} from "@/components/simulations/RunConsole";
import type {SimulationArtefact, SimulationRun} from "@/services/api/simulations";
import {ACTIVITY_ROLE, ActivityFeed, ActivityFeedError} from "@/services/simulations/activityFeed";
import {activityArtefact, createRunConsoleStore, type RunConsoleApi} from "@/state/runConsoleStore";

const bytes = (text: string): Uint8Array => new TextEncoder().encode(text);
const lines = (...values: object[]): string => values.map((v) => JSON.stringify(v)).join("\n") + "\n";

const HEADER = {schema: "ada-activity/1", actors: [{id: "mission", label: "Mission"}]};
const FEED = lines(
    HEADER,
    {actors: [{id: "amr", label: "Logistics robot", color: "#4db3e6"}]},
    {t: 0, actor: "mission", kind: "state", text: "Mission started"},
    {t: 0.2, actor: "amr", kind: "state", text: "Driving into the cell", data: {goal: [1, 2, 0]}},
    {t: 0.2, actor: "amr", kind: "plan", text: "Dock beside the picker"},
    {t: 13.4, actor: "amr", kind: "state", text: "Docked 23 mm off its dock"},
    {t: 13.4, actor: "amr", kind: "plan", text: ""},
    {t: 14, actor: "picker", text: "View 1: no depth image arrived", level: "warn"},
    {t: 20, actor: "mission", kind: "state", text: "Mission failed: the scan produced no depth data", level: "error"},
);

// ----- the feed -----------------------------------------------------------------------------

test("a feed is its actors and, at a moment, what each is doing and planning", () => {
    const feed = new ActivityFeed();
    assert.equal(feed.append(bytes(FEED)), 7);
    // The header's actors first, then one named later with its label and colour, then one only an entry names.
    assert.deepEqual(feed.actors, [
        {id: "mission", label: "Mission", color: ""},
        {id: "amr", label: "Logistics robot", color: "#4db3e6"},
        {id: "picker", label: "picker", color: ""},
    ]);
    assert.equal(feed.duration, 20);
    assert.equal(feed.stateAt("amr", 0.1), undefined, "before its first state it is doing nothing known");
    assert.equal(feed.stateAt("amr", 5)?.text, "Driving into the cell");
    assert.deepEqual(feed.stateAt("amr", 5)?.data, {goal: [1, 2, 0]});
    assert.equal(feed.planAt("amr", 5)?.text, "Dock beside the picker");
    assert.equal(feed.stateAt("amr", 13.4)?.text, "Docked 23 mm off its dock");
    assert.equal(feed.planAt("amr", 14), undefined, "an empty plan takes the plan away");
    assert.equal(feed.stateAt("mission", 99)?.level, "error");
    assert.equal(feed.entries[5].kind, "event", "an entry without a kind is something that happened");
    assert.equal(feed.countUpTo(13.4), 5);
    assert.equal(feed.countUpTo(-1), 0);
});

test("a feed is read as it arrives, cut wherever it was cut", () => {
    const whole = bytes(FEED.replace("Logistics robot", "Logistikkrobot på hjul"));
    const feed = new ActivityFeed();
    let added = 0;
    // Three bytes at a time: through the middle of lines, and of the two-byte "å".
    for (let at = 0; at < whole.length; at += 3) added += feed.append(whole.subarray(at, at + 3));
    assert.equal(added, 7);
    assert.equal(feed.actors[1].label, "Logistikkrobot på hjul");
    // The same answers as read at once.
    const atOnce = new ActivityFeed();
    atOnce.append(whole);
    assert.deepEqual(feed.entries, atOnce.entries);
});

test("a line that is not an entry is left out; a file that is not a feed is refused", () => {
    const feed = new ActivityFeed();
    const text = lines(HEADER) + "not json\n" + lines({actor: "amr", text: "no time"}, {t: 1, text: "no actor"}, {t: 2, actor: "amr", text: "kept"});
    assert.equal(feed.append(bytes(text)), 1);
    assert.equal(feed.entries[0].text, "kept");
    assert.throws(() => new ActivityFeed().append(bytes(lines({schema: "something/1"}))), ActivityFeedError);
    assert.throws(() => new ActivityFeed().append(bytes("glTF binary\n")), ActivityFeedError);
});

test("entries written out of order are shown in order", () => {
    const feed = new ActivityFeed();
    feed.append(bytes(lines(HEADER, {t: 5, actor: "a", kind: "state", text: "later"}, {t: 1, actor: "a", kind: "state", text: "earlier"})));
    assert.deepEqual(feed.entries.map((e) => e.text), ["earlier", "later"]);
    assert.equal(feed.stateAt("a", 2)?.text, "earlier");
});

// ----- what the console shows -----------------------------------------------------------------

test("the console at a moment: a row an actor, and what was said up to then", () => {
    const feed = new ActivityFeed();
    feed.append(bytes(FEED));
    const early = consoleAt(feed, 5);
    assert.deepEqual(
        early.actors.map((a) => [a.actor.id, a.state?.text, a.plan?.text]),
        [
            ["mission", "Mission started", undefined],
            ["amr", "Driving into the cell", "Dock beside the picker"],
            ["picker", undefined, undefined],
        ],
    );
    assert.equal(early.log.length, 3);
    const late = consoleAt(feed, 100, 2);
    assert.deepEqual(late.log.map((e) => e.text), ["View 1: no depth image arrived", "Mission failed: the scan produced no depth data"]);
    assert.equal(late.earlier, 5, "the lines before the ones drawn are counted, not drawn");
});

// ----- the store ------------------------------------------------------------------------------

function run(overrides: Partial<SimulationRun> = {}): SimulationRun {
    return {
        run_id: "run-1",
        provider: {id: "robot", domain: "robotics", engine: "gazebo", version: "1"},
        kind: null,
        parameters: {},
        inputs: [],
        started_by: {id: "me", display: null},
        created_at: "2026-11-13T12:00:00Z",
        started_at: null,
        finished_at: null,
        status: "running",
        stage: "mission",
        progress: 0.5,
        error: null,
        job_id: null,
        artefacts: [],
        summary: {},
        cancel_requested: false,
        ...overrides,
    };
}

/** A server with one run whose feed a test grows. */
function server() {
    const state = {run: run(), blob: new Uint8Array(0) as Uint8Array, reads: [] as number[], revision: 0};
    const artefact = (): SimulationArtefact => ({
        name: "activity.jsonl",
        key: "_simulations/run-1/artefacts/activity.jsonl",
        media_type: "application/x-ndjson",
        role: ACTIVITY_ROLE,
        size: state.blob.byteLength,
        partial: state.run.status === "running",
        revision: state.revision,
    });
    const api: RunConsoleApi = {
        getRun: async () => ({...state.run, artefacts: state.revision ? [artefact()] : []}),
        fetchArtefact: async (_scope, _artefact, range = {}) => {
            const start = range.start ?? 0;
            state.reads.push(start);
            return {bytes: state.blob.slice(start), start, total: state.blob.byteLength};
        },
    };
    const store = (text: string): void => {
        state.blob = bytes(text);
        state.revision++;
    };
    return {state, api, store};
}

/** A schedule a test runs by hand. */
function manual() {
    const waiting: (() => void)[] = [];
    return {
        waiting,
        schedule: (fn: () => void) => {
            waiting.push(fn);
            return () => void waiting.splice(waiting.indexOf(fn), 1);
        },
        fire: async () => {
            const fn = waiting.shift();
            fn?.();
            await new Promise((r) => setTimeout(r, 0));
            await new Promise((r) => setTimeout(r, 0));
        },
    };
}

test("the console follows the run that was opened: only what is new is fetched, until the run has ended", async () => {
    const {state, api, store} = server();
    const clock = manual();
    const useStore = createRunConsoleStore(api, clock.schedule);
    const first = FEED.split("\n").slice(0, 4).join("\n") + "\n";

    useStore.getState().open("shared", state.run, "Weld run");
    await new Promise((r) => setTimeout(r, 0));
    // The run says nothing yet; it is still going, so it is asked again.
    assert.deepEqual([useStore.getState().feed, useStore.getState().live, clock.waiting.length], [null, true, 1]);
    assert.deepEqual(useStore.getState().run, {scope: "shared", runId: "run-1", title: "Weld run"});

    store(first);
    await clock.fire();
    const feed = useStore.getState().feed!;
    assert.equal(feed.entries.length, 2);
    assert.deepEqual(state.reads, [0]);
    const version = useStore.getState().version;

    // Nothing new: nothing fetched.
    await clock.fire();
    assert.deepEqual(state.reads, [0]);
    assert.equal(useStore.getState().version, version);

    // Grown: from where the last read ended, into the same feed.
    store(FEED);
    await clock.fire();
    assert.deepEqual(state.reads, [0, bytes(first).byteLength]);
    assert.equal(useStore.getState().feed, feed);
    assert.equal(feed.entries.length, 7);
    assert.ok(useStore.getState().version > version);

    // The run ends: one more look, and then no more.
    state.run = run({status: "done"});
    await clock.fire();
    assert.deepEqual([useStore.getState().live, clock.waiting.length], [false, 0]);
});

test("opening another run starts over, and a look that answers late is dropped", async () => {
    const {state, api, store} = server();
    const clock = manual();
    const useStore = createRunConsoleStore(api, clock.schedule);
    store(FEED);
    state.run = run({status: "done"});
    let release: (() => void) | null = null;
    const slow: RunConsoleApi = {
        ...api,
        getRun: async (scope, id) => {
            if (id !== "run-1") return run({run_id: id, status: "done"}); // a run that says nothing
            await new Promise<void>((r) => (release = r));
            return api.getRun(scope, id);
        },
    };
    const useSlow = createRunConsoleStore(slow, clock.schedule);
    useSlow.getState().open("shared", state.run);
    useSlow.getState().open("shared", run({run_id: "run-2", status: "done"}));
    await new Promise((r) => setTimeout(r, 0));
    release!();
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(useSlow.getState().run?.runId, "run-2");
    assert.equal(useSlow.getState().feed, null, "the first run's feed did not land in the second's console");

    // A finished run with a feed is read once, whole.
    useStore.getState().open("shared", state.run);
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(useStore.getState().feed?.entries.length, 7);
    assert.equal(clock.waiting.length, 0);
    useStore.getState().close();
    assert.deepEqual([useStore.getState().run, useStore.getState().feed], [null, null]);
});

test("a feed is found by its role, and whether the console is shown is the user's", () => {
    const withFeed = run({artefacts: [{name: "x", key: "k", media_type: "", role: "data", size: 1, partial: false, revision: 1}, {name: "activity.jsonl", key: "a", media_type: "", role: ACTIVITY_ROLE, size: 1, partial: false, revision: 1}]});
    assert.equal(activityArtefact(withFeed)?.key, "a");
    assert.equal(activityArtefact(run()), undefined);
    const useStore = createRunConsoleStore(server().api, manual().schedule);
    assert.equal(useStore.getState().shown, false);
    useStore.getState().setShown(true);
    assert.equal(useStore.getState().shown, true);
});
