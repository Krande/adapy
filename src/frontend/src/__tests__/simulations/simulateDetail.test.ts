/**
 * The connection studio's "Simulate…": when it is enabled (the last successful build of the selected
 * spec has welds, and the library is ready) and what it does (stores the weld assembly through the
 * route, offers the subject, opens the Simulations panel).
 */

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { devConnectionsApi } from "../../services/api/devConnections";
import { simulateAvailability, simulateDetail, subjectLabel } from "../../services/devConnections/simulateDetail";
import { useSimulationStore } from "../../state/simulationStore";
import { useSimulationSubjectStore } from "../../state/simulationSubjects";

const built = (spec: string, welds?: number) => ({ spec_name: spec, counts: welds === undefined ? {} : { welds } });

test("Simulate is enabled for a ready library and a build of the selected spec that has welds", () => {
  assert.deepEqual(simulateAvailability("ready", "box.box_to_box", built("box.box_to_box", 8)), {
    enabled: true,
    reason: null,
  });
});

test("Simulate is disabled, with the reason, when nothing can be offered", () => {
  const cases: [ReturnType<typeof simulateAvailability>, RegExp][] = [
    [simulateAvailability("ready", null, built("a", 4)), /Pick a connection/],
    [simulateAvailability("ready", "a", null), /Nothing built/],
    // The joint on screen is another spec's: it is not the one that would be offered.
    [simulateAvailability("ready", "a", built("b", 4)), /Nothing built/],
    [simulateAvailability("ready", "a", built("a", 0)), /no welds/],
    [simulateAvailability("ready", "a", built("a")), /no welds/],
    [simulateAvailability("reloading", "a", built("a", 4)), /not ready/],
    [simulateAvailability("error", "a", built("a", 4)), /not ready/],
    [simulateAvailability("ready", "a", built("a", 4), true), /Offering/],
  ];
  for (const [availability, reason] of cases) {
    assert.equal(availability.enabled, false);
    assert.match(availability.reason ?? "", reason);
  }
});

test("the label counts the welds", () => {
  assert.equal(subjectLabel("box.box_to_box", 8), "box.box_to_box (8 welds)");
  assert.equal(subjectLabel("x.y", 1), "x.y (1 weld)");
});

const originalSubject = devConnectionsApi.subject;
const originalLoad = useSimulationStore.getState().loadProviders;

beforeEach(() => {
  useSimulationSubjectStore.setState({ offered: [] });
  useSimulationStore.setState({ panelOpen: false });
});

afterEach(() => {
  devConnectionsApi.subject = originalSubject;
  useSimulationStore.setState({ loadProviders: originalLoad, panelOpen: false });
});

test("simulating stores the subject, offers it and opens the Simulations panel", async () => {
  const asked: unknown[] = [];
  let loaded = 0;
  devConnectionsApi.subject = async (spec, inputs) => {
    asked.push([spec, inputs]);
    return {
      scope: "user:me",
      key: "_dev/connections/box.box_to_box/abc.weld-assembly.json",
      kind: "weld-assembly",
      welds: 8,
      label: "box.box_to_box (8 welds)",
    };
  };
  useSimulationStore.setState({
    loadProviders: async () => {
      loaded += 1;
    },
  });

  await simulateDetail("box.box_to_box", { incoming: { section: "BOX200x200x10x10" } });

  assert.deepEqual(asked, [["box.box_to_box", { incoming: { section: "BOX200x200x10x10" } }]]);
  assert.deepEqual(useSimulationSubjectStore.getState().offered, [
    {
      kind: "weld-assembly",
      key: "_dev/connections/box.box_to_box/abc.weld-assembly.json",
      scope: "user:me",
      label: "box.box_to_box (8 welds)",
      source: "connection studio",
    },
  ]);
  assert.equal(loaded, 1);
  assert.equal(useSimulationStore.getState().panelOpen, true);
});

test("a refused offer leaves nothing offered and the panel shut", async () => {
  devConnectionsApi.subject = async () => {
    throw new Error("the detail has no welds");
  };
  await assert.rejects(simulateDetail("a", {}), /no welds/);
  assert.deepEqual(useSimulationSubjectStore.getState().offered, []);
  assert.equal(useSimulationStore.getState().panelOpen, false);
});
