// A saved-group member's SOURCE: the stored document its geometry was derived from, pinned to the
// revision it was saved against, and the member's own id in it (`MemberSource`, `./savedGroups`).
//
// WHY A REFERENCE AND NOT A COPY. A published revision is immutable: `assets/<collection>/<subject>/
// <revision>/` is written once, and core never removes one by itself -- only a person does, by
// unpublishing it or deleting its source (`routes/assets.py`). A reference with the revision pinned
// therefore names exactly the bytes the member was picked from, at no storage cost, and a consumer
// with the provider's reader gets the full solid-model (or STEP / IFC / Genie XML) objects from it.
// Copying a subset of a source would need a writer per format -- core has none for a provider's
// private format, by design (`ada/assets/manifest.py`) -- and the copy would be a second, diverging
// source of truth.
//
// WHERE THE SOURCE IS FOUND. A published node: its subject's manifest (`asset.json`), the artefact
// with role `source` -- the one role every publisher that keeps its source uses for it. A plain
// stored file (a STEP / IFC / XML upload the viewer converted): the file itself; it has no
// revisions. A file opened from disk, or a stored GLB: nothing to refer to, and the member says so.
//
// Pure apart from the injected reads, so it runs under `node --test`.

import { assetKey } from "@/assets/keys";

import { memberKey, sourceFormatOf, type GroupMember, type GroupMemberTarget, type MemberSource } from "./savedGroups";

type NodeTarget = Extract<GroupMemberTarget, { kind: "node" }>;

/** The manifest file of one published subject-revision (`ada.assets.manifest.MANIFEST_FILENAME`). */
export const MANIFEST_FILENAME = "asset.json";

/** The artefact role a publisher stores its source file under. */
export const SOURCE_ROLE = "source";

export interface SourceRefDeps {
  /** The scope the members are stored in, in its wire form -- recorded in each reference. */
  readonly scope: string;
  /** The manifest of one subject-revision, parsed JSON. Throws when it cannot be read. */
  readManifest(collection: string, subject: string, revision: string): Promise<unknown>;
  /** The revision a node resolves to now, for a member that names none. */
  currentRevision?(target: NodeTarget): Promise<string | null>;
  /** Element members -> their row id in the published tree, by `memberKey` -- the members it
   *  cannot place are absent. Best effort: a failure places nothing. */
  elementIds?(members: readonly GroupMember[]): Promise<ReadonlyMap<string, string>>;
  /** Whether a file target's source name is a file opened from disk, not a stored key. */
  isLocalFile?(sourceKey: string): boolean;
}

/** The source artefact of a parsed manifest: its storage key and hash, or null when it names
 *  none. A sibling `file` is resolved against the revision's own prefix. */
export function sourceArtefactOf(
  manifest: unknown,
  collection: string,
  subject: string,
  revision: string,
): { readonly key: string; readonly sha256: string | null } | null {
  if (!manifest || typeof manifest !== "object") return null;
  const artefacts = (manifest as { artefacts?: unknown }).artefacts;
  if (!Array.isArray(artefacts)) return null;
  for (const a of artefacts) {
    if (!a || typeof a !== "object") continue;
    const entry = a as { role?: unknown; key?: unknown; file?: unknown; sha256?: unknown };
    if (entry.role !== SOURCE_ROLE) continue;
    const sha = typeof entry.sha256 === "string" && entry.sha256 ? entry.sha256 : null;
    if (typeof entry.key === "string" && entry.key) return { key: entry.key, sha256: sha };
    if (typeof entry.file === "string" && entry.file) {
      try {
        return { key: assetKey(collection, subject, revision, entry.file), sha256: sha };
      } catch {
        return null;
      }
    }
  }
  return null;
}

function fileName(key: string): string {
  return key.split("/").pop() ?? key;
}

/** `members` with `source` recorded on each that has none yet. Never throws: a member whose source
 *  cannot be found is recorded as such (`kind: "none"`), and the members come back in order. One
 *  manifest read per subject-revision, one element-id lookup for all of them. */
export async function attachMemberSources(
  members: readonly GroupMember[],
  deps: SourceRefDeps,
): Promise<GroupMember[]> {
  const todo = members.filter((m) => !m.source);
  if (todo.length === 0) return [...members];

  // The revision each node member is pinned to: its own, else what it resolves to now.
  const revisions = new Map<string, Promise<string | null>>();
  const revisionOf = (t: NodeTarget): Promise<string | null> => {
    if (t.revision) return Promise.resolve(t.revision);
    const key = `${t.provider}\u0000${t.collection}\u0000${t.subject}`;
    let p = revisions.get(key);
    if (!p) {
      p = (deps.currentRevision ? deps.currentRevision(t) : Promise.resolve(null)).catch(() => null);
      revisions.set(key, p);
    }
    return p;
  };
  const manifests = new Map<string, Promise<{ ok: true; value: unknown } | { ok: false; why: string }>>();
  const manifestOf = (collection: string, subject: string, revision: string) => {
    const key = `${collection}\u0000${subject}\u0000${revision}`;
    let p = manifests.get(key);
    if (!p) {
      p = deps.readManifest(collection, subject, revision).then(
        (value) => ({ ok: true as const, value }),
        (e) => ({ ok: false as const, why: e instanceof Error ? e.message : String(e) }),
      );
      manifests.set(key, p);
    }
    return p;
  };

  const elements = todo.filter((m) => m.target.kind === "node" && m.element !== null);
  let ids: ReadonlyMap<string, string> = new Map();
  if (elements.length && deps.elementIds) {
    try {
      ids = await deps.elementIds(elements);
    } catch {
      ids = new Map();
    }
  }

  const sourceFor = async (m: GroupMember): Promise<MemberSource> => {
    const t = m.target;
    if (t.kind === "file") {
      if (deps.isLocalFile?.(t.source_key)) {
        return {
          kind: "none",
          reason: "local-file",
          detail: `${fileName(t.source_key)} was opened from disk; nothing of it is stored, so there is no source to refer to`,
        };
      }
      if (/\.(glb|gltf)$/i.test(t.source_key)) {
        return {
          kind: "none",
          reason: "mesh-only",
          detail: `${fileName(t.source_key)} is a GLB: triangles only, with no solid model behind it`,
        };
      }
      return {
        kind: "stored",
        scope: deps.scope,
        provider: null,
        collection: null,
        subject: null,
        revision: null,
        key: t.source_key,
        format: sourceFormatOf(t.source_key),
        sha256: null,
        element_id: null,
      };
    }
    const revision = await revisionOf(t);
    if (!revision) {
      return { kind: "none", reason: "unreadable", detail: `no published revision of ${t.collection}/${t.subject} was found` };
    }
    const read = await manifestOf(t.collection, t.subject, revision);
    if (!read.ok) {
      return { kind: "none", reason: "unreadable", detail: `the manifest of ${t.subject} @ ${revision} could not be read: ${read.why}` };
    }
    const artefact = sourceArtefactOf(read.value, t.collection, t.subject, revision);
    if (!artefact) {
      return {
        kind: "none",
        reason: "no-source-artefact",
        detail: `${t.subject} @ ${revision} was published without a source file (geometry only)`,
      };
    }
    return {
      kind: "stored",
      scope: deps.scope,
      provider: t.provider,
      collection: t.collection,
      subject: t.subject,
      revision,
      key: artefact.key,
      format: sourceFormatOf(artefact.key),
      sha256: artefact.sha256,
      element_id: m.element === null ? (t.node ?? t.subject) : (ids.get(memberKey(m)) ?? null),
    };
  };

  const out: GroupMember[] = [];
  for (const m of members) {
    if (m.source) {
      out.push(m);
      continue;
    }
    let source: MemberSource;
    try {
      source = await sourceFor(m);
    } catch (e) {
      source = { kind: "none", reason: "unreadable", detail: e instanceof Error ? e.message : String(e) };
    }
    out.push({ ...m, source });
  }
  return out;
}

// --- what the panel says -------------------------------------------------------------------------

export interface SourceLine {
  /** One source document (or one reason there is none), for grouping. */
  readonly key: string;
  readonly text: string;
  readonly count: number;
  /** A source in the store; false for "none" and "not recorded". */
  readonly stored: boolean;
}

/** Where the members' source geometry is, one line per source document, in first-seen order:
 *  "source: model.db of S10 @ 20260101T000000Z, 3 members". */
export function memberSourceLines(members: readonly GroupMember[]): SourceLine[] {
  const by = new Map<string, { text: string; count: number; stored: boolean }>();
  const n = (count: number) => `${count} member${count === 1 ? "" : "s"}`;
  for (const m of members) {
    const s = m.source;
    let key: string;
    let text: string;
    let stored = false;
    if (!s) {
      key = "unrecorded";
      text = "source not recorded (saved before sources were kept)";
    } else if (s.kind === "stored") {
      stored = true;
      key = `stored:${s.scope}:${s.key}:${s.revision ?? ""}:${s.subject ?? ""}`;
      text =
        s.subject && s.revision
          ? `source: ${fileName(s.key)} of ${s.subject} @ ${s.revision}`
          : `source: ${fileName(s.key)} (stored file, not versioned)`;
    } else if (s.reason === "local-file") {
      key = `none:${s.reason}:${m.target.kind === "file" ? m.target.source_key : ""}`;
      text = `no source in the store: ${m.target.kind === "file" ? fileName(m.target.source_key) : "this model"} was opened from a local file`;
    } else {
      key = `none:${s.reason}:${s.detail}`;
      text = `no source geometry: ${s.detail}`;
    }
    const entry = by.get(key);
    if (entry) entry.count++;
    else by.set(key, { text, count: 1, stored });
  }
  return [...by.entries()].map(([key, v]) => ({ key, text: `${v.text}, ${n(v.count)}`, count: v.count, stored: v.stored }));
}

/** Whether any member still lacks a recorded source -- what offers "Record sources". */
export function hasUnrecordedSources(members: readonly GroupMember[]): boolean {
  return members.some((m) => !m.source);
}

// --- what a server check can read ----------------------------------------------------------------

/** The members a SERVER check can read, in the group wire shape, and those it cannot: a member
 *  opened from a local file is in no store the server reads. A node member that names no revision
 *  is pinned to its source's, so the check reads the source the member was saved against. */
export function serverCheckMembers(members: readonly GroupMember[]): {
  readonly members: GroupMember[];
  readonly skipped: GroupMember[];
} {
  const out: GroupMember[] = [];
  const skipped: GroupMember[] = [];
  for (const m of members) {
    if (m.source?.kind === "none" && m.source.reason === "local-file") {
      skipped.push(m);
      continue;
    }
    const t = m.target;
    if (t.kind === "node" && !t.revision && m.source?.kind === "stored" && m.source.revision && m.source.subject === t.subject) {
      out.push({ ...m, target: { ...t, revision: m.source.revision } });
      continue;
    }
    out.push(m);
  }
  return { members: out, skipped };
}
