import assert from "node:assert/strict";
import { test } from "node:test";

import type { WorkerEntry } from "@/services/viewerApi";
import { groupWorkersByImage, describeImagePool } from "@/components/admin/auditPools";

// The audit-run pool picker used to list capability tags, which chose a FLEET
// back when each capability was its own image. One combined image advertising
// six capabilities turned that into six choices resolving to the same pods.

const worker = (over: Partial<WorkerEntry>): WorkerEntry => ({
  worker_id: "w",
  image_tag: "img-a",
  capabilities: ["base"],
  started_at: 0,
  last_heartbeat: 0,
  online: true,
  ...over,
});

test("replicas of one image collapse to a single choice", () => {
  const pools = groupWorkersByImage([
    worker({ worker_id: "a", capabilities: ["base", "weld-gen", "abaqus"] }),
    worker({ worker_id: "b", capabilities: ["base", "weld-gen", "abaqus"] }),
  ]);
  assert.equal(pools.length, 1, "one image should be one choice");
  assert.equal(pools[0].replicas, 2);
  assert.deepEqual(pools[0].capabilities, ["abaqus", "base", "weld-gen"]);
});

test("offline workers are not offered", () => {
  // An absent pod's image must not appear: dispatching to it would queue work
  // nothing will pull.
  const pools = groupWorkersByImage([
    worker({ worker_id: "a" }),
    worker({ worker_id: "b", image_tag: "img-gone", online: false }),
  ]);
  assert.deepEqual(pools.map((p) => p.imageTag), ["img-a"]);
});

test("a sweep routes to base when the image serves it", () => {
  const pools = groupWorkersByImage([worker({ capabilities: ["weld-gen", "base"] })]);
  assert.equal(pools[0].routeCapability, "base");
});

test("a specialised image is still reachable via its own capability", () => {
  const pools = groupWorkersByImage([worker({ image_tag: "wg", capabilities: ["weld-gen"] })]);
  assert.equal(pools[0].routeCapability, "weld-gen");
});

test("two images sharing a route are marked unenforceable", () => {
  // Routing is by NATS subject, so both images consume the same one. The UI
  // must not imply a restriction it cannot apply.
  const pools = groupWorkersByImage([
    worker({ worker_id: "a", image_tag: "img-a" }),
    worker({ worker_id: "b", image_tag: "img-b" }),
  ]);
  assert.equal(pools.length, 2);
  assert.ok(pools.every((p) => !p.enforceable), "a shared subject cannot bind to one image");
});

test("a sole provider is enforceable", () => {
  const pools = groupWorkersByImage([
    worker({ worker_id: "a", image_tag: "img-a", capabilities: ["base"] }),
    worker({ worker_id: "b", image_tag: "img-b", capabilities: ["weld-gen"] }),
  ]);
  assert.ok(pools.every((p) => p.enforceable));
});

test("the busiest fleet is offered first", () => {
  const pools = groupWorkersByImage([
    worker({ worker_id: "a", image_tag: "small", capabilities: ["weld-gen"] }),
    worker({ worker_id: "b", image_tag: "big", capabilities: ["base"] }),
    worker({ worker_id: "c", image_tag: "big", capabilities: ["base"] }),
  ]);
  assert.equal(pools[0].imageTag, "big");
});

test("a worker with no image tag is still listed, not dropped", () => {
  const pools = groupWorkersByImage([worker({ image_tag: null })]);
  assert.equal(pools.length, 1);
  assert.match(describeImagePool(pools[0]), /no image tag/);
});

test("the label carries the fleet size", () => {
  const pools = groupWorkersByImage([worker({ worker_id: "a" }), worker({ worker_id: "b" })]);
  assert.match(describeImagePool(pools[0]), /img-a — 2 replicas/);
});
