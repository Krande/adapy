import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyPlacement,
  bandBounds,
  memberToBands,
  stationRingPoints,
  type LoftMemberDoc,
  type LoftStation,
} from "../../utils/cellbuilder/loft";

test("rectangle station ring = 4 corners centred at X/Y in the z==Z plane", () => {
  const st: LoftStation = {
    TYPE: "rectangle",
    X: 1,
    Y: 2,
    Z: 5,
    WIDTH: 4,
    HEIGHT: 2,
  };
  const ring = stationRingPoints(st);
  assert.equal(ring.length, 4);
  // corners at (X ± W/2, Y ± H/2, Z), order (-,-),(+,-),(+,+),(-,+)
  assert.deepEqual(ring[0], [-1, 1, 5]);
  assert.deepEqual(ring[1], [3, 1, 5]);
  assert.deepEqual(ring[2], [3, 3, 5]);
  assert.deepEqual(ring[3], [-1, 3, 5]);
});

test("circle station ring = SEGMENTS points of RADIUS around X/Y", () => {
  const st: LoftStation = {
    TYPE: "circle",
    X: 0,
    Y: 0,
    Z: 0,
    RADIUS: 2,
    SEGMENTS: 8,
  };
  const ring = stationRingPoints(st);
  assert.equal(ring.length, 8);
  // first sample at angle 0 -> (X+R, Y, Z)
  assert.ok(Math.abs(ring[0][0] - 2) < 1e-9);
  assert.ok(Math.abs(ring[0][1] - 0) < 1e-9);
  assert.equal(ring[0][2], 0);
  // every point sits on the radius circle in the z==Z plane
  for (const p of ring) {
    assert.ok(Math.abs(Math.hypot(p[0], p[1]) - 2) < 1e-9);
    assert.equal(p[2], 0);
  }
});

test("circle SEGMENTS defaults to 16 and floors to >= 3", () => {
  assert.equal(
    stationRingPoints({ TYPE: "circle", X: 0, Y: 0, Z: 0, RADIUS: 1 }).length,
    16,
  );
  assert.equal(
    stationRingPoints({
      TYPE: "circle",
      X: 0,
      Y: 0,
      Z: 0,
      RADIUS: 1,
      SEGMENTS: 2,
    }).length,
    3,
  );
});

test("applyPlacement is identity for a null matrix, and applies a row-major affine", () => {
  assert.deepEqual(applyPlacement(null, [1, 2, 3]), [1, 2, 3]);
  // translate by (10, 20, 30)
  const t = [
    [1, 0, 0, 10],
    [0, 1, 0, 20],
    [0, 0, 1, 30],
    [0, 0, 0, 1],
  ];
  assert.deepEqual(applyPlacement(t, [1, 2, 3]), [11, 22, 33]);
  // 90deg rotation about Z (row-major): (1,0,0) -> (0,1,0)
  const rz = [
    [0, -1, 0, 0],
    [1, 0, 0, 0],
    [0, 0, 1, 0],
    [0, 0, 0, 1],
  ];
  const r = applyPlacement(rz, [1, 0, 0]);
  assert.ok(Math.abs(r[0]) < 1e-9);
  assert.ok(Math.abs(r[1] - 1) < 1e-9);
});

test("PLACEMENT is baked into a station ring (nested 4x4 and flat 16 agree)", () => {
  const st: LoftStation = {
    TYPE: "rectangle",
    X: 0,
    Y: 0,
    Z: 0,
    WIDTH: 2,
    HEIGHT: 2,
  };
  const nested = [
    [1, 0, 0, 100],
    [0, 1, 0, 0],
    [0, 0, 1, 0],
    [0, 0, 0, 1],
  ];
  const flat = [1, 0, 0, 100, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const a = stationRingPoints(st, nested);
  const b = stationRingPoints(st, flat);
  assert.deepEqual(a, b);
  // corner (-1,-1,0) shifted +100 in X
  assert.deepEqual(a[0], [99, -1, 0]);
});

test("memberToBands splits N stations into N-1 bays with matching rings + ids", () => {
  const member: LoftMemberDoc = {
    NAME: "FLOATER",
    STATIONS: [
      { TYPE: "rectangle", X: 0, Y: 0, Z: 0, WIDTH: 2, HEIGHT: 2 },
      { TYPE: "rectangle", X: 0, Y: 0, Z: 3, WIDTH: 2, HEIGHT: 2 },
      { TYPE: "rectangle", X: 0, Y: 0, Z: 6, WIDTH: 4, HEIGHT: 4 },
    ],
  };
  const bands = memberToBands(member);
  assert.equal(bands.length, 2); // 3 stations -> 2 bays
  assert.equal(bands[0].cellName, "FLOATER_bay0");
  assert.equal(bands[1].cellName, "FLOATER_bay1");
  assert.equal(bands[0].bandCount, 2);
  // band i is swept STATIONS[i] -> STATIONS[i+1]
  assert.equal(bands[0].bay, 0);
  assert.deepEqual(bands[0].rings[0], stationRingPoints(member.STATIONS[0]));
  assert.deepEqual(bands[0].rings[1], stationRingPoints(member.STATIONS[1]));
  assert.deepEqual(bands[1].rings[0], stationRingPoints(member.STATIONS[1]));
  assert.deepEqual(bands[1].rings[1], stationRingPoints(member.STATIONS[2]));
  // station metadata carried through for the info panel
  assert.equal(bands[1].stationLo, member.STATIONS[1]);
  assert.equal(bands[1].stationHi, member.STATIONS[2]);
});

test("memberToBands respects INCLUDE=false and needs >= 2 stations", () => {
  const base: LoftStation[] = [
    { TYPE: "circle", X: 0, Y: 0, Z: 0, RADIUS: 1 },
    { TYPE: "circle", X: 0, Y: 0, Z: 2, RADIUS: 1 },
  ];
  assert.deepEqual(
    memberToBands({ NAME: "SKIP", INCLUDE: false, STATIONS: base }),
    [],
  );
  assert.deepEqual(memberToBands({ NAME: "ONE", STATIONS: [base[0]] }), []);
  assert.equal(memberToBands({ NAME: "OK", STATIONS: base }).length, 1);
});

test("memberToBands applies the member PLACEMENT to every band ring", () => {
  const member: LoftMemberDoc = {
    NAME: "M",
    PLACEMENT: [
      [1, 0, 0, 5],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1],
    ],
    STATIONS: [
      { TYPE: "rectangle", X: 0, Y: 0, Z: 0, WIDTH: 2, HEIGHT: 2 },
      { TYPE: "rectangle", X: 0, Y: 0, Z: 1, WIDTH: 2, HEIGHT: 2 },
    ],
  };
  const [band] = memberToBands(member);
  // lower ring's first corner (-1,-1,0) shifted +5 in X
  assert.deepEqual(band.rings[0][0], [4, -1, 0]);
});

test("bandBounds gives the AABB (min corner + size) of both rings", () => {
  const member: LoftMemberDoc = {
    NAME: "M",
    STATIONS: [
      { TYPE: "rectangle", X: 0, Y: 0, Z: 0, WIDTH: 2, HEIGHT: 2 },
      { TYPE: "rectangle", X: 0, Y: 0, Z: 4, WIDTH: 6, HEIGHT: 2 },
    ],
  };
  const [band] = memberToBands(member);
  const { origin, size } = bandBounds(band);
  assert.deepEqual(origin, [-3, -1, 0]); // widest half-width 3, z from 0
  assert.deepEqual(size, [6, 2, 4]);
});
