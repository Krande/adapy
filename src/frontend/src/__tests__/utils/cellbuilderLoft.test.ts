import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyPlacement,
  bandBounds,
  insertStation,
  memberToBands,
  removeStation,
  setStationParam,
  stationRingPoints,
  translateMember,
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

// --- Phase 3a: pure loft-editing helpers -----------------------------------

const threeStations = (): LoftStation[] => [
  { TYPE: "rectangle", X: 0, Y: 0, Z: 0, WIDTH: 2, HEIGHT: 2 },
  { TYPE: "rectangle", X: 0, Y: 0, Z: 3, WIDTH: 2, HEIGHT: 2 },
  { TYPE: "rectangle", X: 0, Y: 0, Z: 6, WIDTH: 4, HEIGHT: 4 },
];

test("setStationParam changes only that station's ring, keeps others identical", () => {
  const member: LoftMemberDoc = { NAME: "M", STATIONS: threeStations() };
  const next = setStationParam(member, 1, "WIDTH", 8);
  // A new member + new STATIONS array (immutable — for undo/zustand).
  assert.notEqual(next, member);
  assert.notEqual(next.STATIONS, member.STATIONS);
  assert.equal(next.STATIONS[1].WIDTH, 8);
  // Stations 0 and 2 are untouched (same objects), station 1 is a new object.
  assert.equal(next.STATIONS[0], member.STATIONS[0]);
  assert.equal(next.STATIONS[2], member.STATIONS[2]);
  assert.notEqual(next.STATIONS[1], member.STATIONS[1]);
  // Only station 1's ring changes.
  const before = memberToBands(member);
  const after = memberToBands(next);
  assert.deepEqual(after[0].rings[0], before[0].rings[0]); // station 0 ring
  assert.notDeepEqual(after[0].rings[1], before[0].rings[1]); // station 1 ring
  assert.deepEqual(after[1].rings[1], before[1].rings[1]); // station 2 ring
});

test("setStationParam clamps WIDTH/HEIGHT/RADIUS to >= 0 but allows negative Z", () => {
  const member: LoftMemberDoc = { NAME: "M", STATIONS: threeStations() };
  assert.equal(setStationParam(member, 0, "WIDTH", -5).STATIONS[0].WIDTH, 0);
  assert.equal(setStationParam(member, 0, "Z", -5).STATIONS[0].Z, -5);
  // A no-op set returns the SAME member ref (no spurious undo step).
  assert.equal(setStationParam(member, 0, "WIDTH", 2), member);
  // Out-of-range index is a no-op.
  assert.equal(setStationParam(member, 9, "WIDTH", 1), member);
});

test("insertStation (interior) adds a midpoint bay and preserves the other bays", () => {
  const member: LoftMemberDoc = { NAME: "M", STATIONS: threeStations() };
  const before = memberToBands(member);
  const next = insertStation(member, 0); // split bay 0 (s0 -> s1)
  assert.equal(next.STATIONS.length, 4);
  const after = memberToBands(next);
  assert.equal(after.length, before.length + 1); // one more bay
  // The inserted station is the midpoint of s0 and s1 (Z 0 and 3 -> 1.5).
  assert.equal(next.STATIONS[1].Z, 1.5);
  // Bay 0's lo ring is unchanged; the untouched trailing bay's geometry is
  // preserved (original bay1 s1->s2 == new bay2).
  assert.deepEqual(after[0].rings[0], before[0].rings[0]);
  assert.deepEqual(after[2].rings[0], before[1].rings[0]);
  assert.deepEqual(after[2].rings[1], before[1].rings[1]);
});

test("insertStation past the last station duplicates it (extends the member)", () => {
  const member: LoftMemberDoc = { NAME: "M", STATIONS: threeStations() };
  const next = insertStation(member, 2); // after the last station
  assert.equal(next.STATIONS.length, 4);
  // Z steps by the last spacing (6 - 3 = 3) -> 9.
  assert.equal(next.STATIONS[3].Z, 9);
  assert.equal(next.STATIONS[3].WIDTH, member.STATIONS[2].WIDTH);
});

test("removeStation drops a station, merges bays, refuses below 2", () => {
  const member: LoftMemberDoc = { NAME: "M", STATIONS: threeStations() };
  const next = removeStation(member, 1);
  assert.equal(next.STATIONS.length, 2);
  assert.equal(memberToBands(next).length, 1);
  // The surviving stations keep their geometry (s0 and s2).
  assert.deepEqual(
    memberToBands(next)[0].rings[1],
    memberToBands(member)[1].rings[1],
  );
  // A 2-station member refuses removal (same ref -> no-op / no undo step).
  const twoStation: LoftMemberDoc = {
    NAME: "M",
    STATIONS: threeStations().slice(0, 2),
  };
  assert.equal(removeStation(twoStation, 0), twoStation);
});

test("translateMember shifts every ring by delta and round-trips as a nested 4x4", () => {
  const member: LoftMemberDoc = { NAME: "M", STATIONS: threeStations() };
  const before = memberToBands(member);
  const next = translateMember(member, [10, -4, 2]);
  // PLACEMENT is a nested 4x4 (what the backend TopoLoftMember accepts).
  assert.ok(Array.isArray(next.PLACEMENT));
  const mat = next.PLACEMENT as number[][];
  assert.equal(mat.length, 4);
  assert.ok(mat.every((r) => r.length === 4));
  assert.deepEqual([mat[0][3], mat[1][3], mat[2][3]], [10, -4, 2]);
  // Every band ring point is shifted by exactly delta.
  const after = memberToBands(next);
  for (let b = 0; b < before.length; b++) {
    for (let r = 0; r < 2; r++) {
      for (let i = 0; i < before[b].rings[r].length; i++) {
        const p0 = before[b].rings[r][i];
        const p1 = after[b].rings[r][i];
        assert.ok(Math.abs(p1[0] - (p0[0] + 10)) < 1e-9);
        assert.ok(Math.abs(p1[1] - (p0[1] - 4)) < 1e-9);
        assert.ok(Math.abs(p1[2] - (p0[2] + 2)) < 1e-9);
      }
    }
  }
  // A zero delta is a no-op (same ref).
  assert.equal(translateMember(member, [0, 0, 0]), member);
});

test("translateMember composes with an existing PLACEMENT (adds to its column)", () => {
  const member: LoftMemberDoc = {
    NAME: "M",
    PLACEMENT: [
      [1, 0, 0, 5],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1],
    ],
    STATIONS: threeStations().slice(0, 2),
  };
  const next = translateMember(member, [0, 3, 0]);
  const mat = next.PLACEMENT as number[][];
  assert.deepEqual([mat[0][3], mat[1][3], mat[2][3]], [5, 3, 0]);
  // Does not mutate the source matrix.
  assert.equal((member.PLACEMENT as number[][])[1][3], 0);
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
