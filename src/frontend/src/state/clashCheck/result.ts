// The clash-check store's result model (`../clashCheckStore`): `ada.clash/result@1` parsed into
// the browser's shape, once, at read time.

import type { WireClashContact, WireClashPass, WireClashApplicableSpec, WireClashGroup, WireClashJoint, WireClashResult } from "@/services/api/clashCheck";

// ---------------------------------------------------------------------------------------------
// The browser's model. Camel-cased, `applicable` normalised (`capability` stays `null` rather
// than `undefined` so every reader tests one falsy shape, not two), and `jointsById` precomputed
// ONCE at parse time -- every group-level derivation below reads through it instead of
// rescanning `joints`, so two call sites deriving the same fact can never disagree.
// ---------------------------------------------------------------------------------------------

export interface ClashApplicableSpec {
  readonly spec: string;
  /** `null` = a built-in spec, runs in core's default pool, always offered
   *  (`ada/clash/builtin_specs.py`). A string names the capability an out-of-tree spec needs a
   *  live pool to advertise -- see `isSpecAvailable`. */
  readonly capability: string | null;
  readonly tags: readonly string[];
  readonly priority: number;
}

export interface ClashJointMember {
  readonly name: string;
  readonly kind: string; // "BEAM" | "PLATE"
  readonly guid: string | null;
  readonly section: string | null;
  readonly memberType: string | null;
  /** `incoming` | `landing` where the checker resolved it; `null` where it did not. */
  readonly role: string | null;
  /** `start` | `end`: which end of this member is at the contact. */
  readonly end: string | null;
}

/** What a geometric pass measured at a contact. Absent fields were NOT measured. */
export interface ClashContact {
  readonly normal: readonly [number, number, number] | null;
  readonly penetrationDepth: number | null;
  readonly nearPoints: readonly (readonly [number, number, number])[];
  readonly contactArea: number | null;
  readonly securityMargin: number | null;
  readonly incomingAngleDeg: number | null;
  readonly extras: Readonly<Record<string, unknown>>;
}

export interface ClashJoint {
  readonly id: string;
  readonly centre: readonly [number, number, number];
  readonly members: readonly ClashJointMember[];
  readonly typeKey: string;
  readonly typeLabel: string;
  readonly applicable: readonly ClashApplicableSpec[];
  /** WHICH PASS found this joint. Two passes over one model find overlapping but different
   *  joints -- a shared node and a two-millimetre overlap are both true -- so a reader who cannot
   *  tell them apart can neither judge a joint nor filter it out. */
  readonly origin: string;
  /** What a geometric pass measured at the contact (normal, penetration depth, patch area), or
   *  null for a pass that works on axes. Shown, never used to decide anything here. */
  readonly contact: ClashContact | null;
}

/** One pass the check knew about, and what became of it. */
export interface ClashPassReport {
  readonly name: string;
  readonly ran: boolean;
  readonly found: number | null;
  readonly reason: string | null;
  readonly capability: string | null;
}

export interface ClashGroup {
  readonly typeKey: string;
  readonly typeLabel: string;
  readonly count: number;
  readonly jointIds: readonly string[];
  readonly applicable: readonly ClashApplicableSpec[];
}

export interface ClashResult {
  readonly schema: string;
  readonly sourceKey: string;
  readonly sourceSha256: string | null;
  readonly options: Readonly<Record<string, unknown>>;
  /** OMITS what was not measured -- see the module doc on `WireClashResult`. Read through
   *  `"members" in counts` before comparing to zero: an ABSENT key means a pass never ran, a
   *  PRESENT zero means it ran and found nothing. */
  readonly counts: Readonly<Record<string, number>>;
  readonly joints: readonly ClashJoint[];
  readonly groups: readonly ClashGroup[];
  readonly provenance: Readonly<Record<string, unknown>>;
  readonly warnings: readonly string[];
  /** Every pass the check knew about, run or not -- what the producer filter is built from. */
  readonly passes: readonly ClashPassReport[];
  /** The checker that ran (`adapy` for core's, which an @1 document implies). */
  readonly checker: string;
  readonly checkerCapability: string | null;
  readonly jointsById: ReadonlyMap<string, ClashJoint>;
}

export class ClashResultError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClashResultError";
  }
}

/** Every schema this viewer reads. @2 only ADDS optional fields, so an @1 document (a cached
 *  result, or the browser check's) reads as an @2 one with them absent -- which it is. */
const READABLE_CLASH_RESULT_SCHEMAS: readonly string[] = ["ada.clash/result@1", "ada.clash/result@2"];

/** Core's own checker id (`ada/clash/builtin_names.py`). */
export const BUILTIN_CHECKER = "adapy";

function parseContact(c: WireClashContact | null | undefined): ClashContact | null {
  if (!c) return null;
  return {
    normal: c.normal ?? null,
    penetrationDepth: c.penetration_depth ?? null,
    nearPoints: c.near_points ?? [],
    contactArea: c.contact_area ?? null,
    securityMargin: c.security_margin ?? null,
    incomingAngleDeg: c.incoming_angle_deg ?? null,
    // An @1 contact was an untyped bag; whatever is not in the @2 vocabulary is kept as an extra
    // rather than dropped, the same rule `ClashContact.from_dict` holds in Python.
    extras: {
      ...Object.fromEntries(
        Object.entries(c).filter(
          ([k]) =>
            ![
              "normal",
              "penetration_depth",
              "near_points",
              "contact_area",
              "security_margin",
              "incoming_angle_deg",
              "extras",
            ].includes(k),
        ),
      ),
      ...(c.extras ?? {}),
    },
  };
}

function parseApplicable(entries: readonly WireClashApplicableSpec[] | undefined): readonly ClashApplicableSpec[] {
  return (entries ?? []).map((a) => ({
    spec: a.spec,
    capability: a.capability ?? null,
    tags: a.tags ?? [],
    priority: a.priority ?? 0,
  }));
}

/** Parse a result document. An unknown schema is REFUSED, never partially read -- the same rule
 *  `parseBuildSummary`/`parseHierarchy` hold elsewhere in this viewer: a document core cannot
 *  fully understand is not "mostly fine" (mirrors `ada.clash.result.parse_clash_result`). */
export function parseClashResult(doc: unknown): ClashResult {
  if (typeof doc !== "object" || doc === null || Array.isArray(doc)) {
    throw new ClashResultError(`clash result must be a JSON object, got ${doc === null ? "null" : typeof doc}`);
  }
  const raw = doc as Partial<WireClashResult>;
  if (typeof raw.schema !== "string" || !READABLE_CLASH_RESULT_SCHEMAS.includes(raw.schema)) {
    throw new ClashResultError(
      `unknown clash result schema ${JSON.stringify(raw.schema)}: this viewer reads ` +
        `${READABLE_CLASH_RESULT_SCHEMAS.map((s) => JSON.stringify(s)).join(", ")} only. ` +
        "Refusing rather than reading the fields it recognises.",
    );
  }
  const joints: ClashJoint[] = (raw.joints ?? []).map((j: WireClashJoint) => ({
    id: j.id,
    centre: j.centre,
    members: (j.members ?? []).map((m) => ({
      name: m.name,
      kind: m.kind,
      guid: m.guid ?? null,
      section: m.section ?? null,
      memberType: m.member_type ?? null,
      role: m.role ?? null,
      end: m.end ?? null,
    })),
    typeKey: j.type_key,
    typeLabel: j.type_label,
    applicable: parseApplicable(j.applicable),
    // Defaulted, not required: a document written before joints carried their producer still
    // reads, and reads as what it was -- core's beam pass was the only one there.
    origin: j.origin ?? "beam-beam",
    contact: parseContact(j.contact),
  }));
  const jointsById = new Map(joints.map((j) => [j.id, j] as const));
  const groups: ClashGroup[] = (raw.groups ?? []).map((g: WireClashGroup) => ({
    typeKey: g.type_key,
    typeLabel: g.type_label,
    count: g.count,
    jointIds: g.joint_ids ?? [],
    applicable: parseApplicable(g.applicable),
  }));
  return {
    schema: raw.schema,
    sourceKey: raw.source_key ?? "",
    sourceSha256: raw.source_sha256 ?? null,
    options: raw.options ?? {},
    counts: raw.counts ?? {},
    joints,
    groups,
    provenance: raw.provenance ?? {},
    warnings: raw.warnings ?? [],
    passes: (raw.passes ?? []).map((p: WireClashPass) => ({
      name: p.name,
      ran: p.ran,
      found: p.found ?? null,
      reason: p.reason ?? null,
      capability: p.capability ?? null,
    })),
    checker: raw.checker ?? BUILTIN_CHECKER,
    checkerCapability: raw.checker_capability ?? null,
    jointsById,
  };
}
