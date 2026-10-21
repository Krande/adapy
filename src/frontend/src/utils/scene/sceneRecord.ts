/**
 * The record of what is loaded in the scene, kept per scope in this browser so a reload can load
 * it again: the shape, its validation, and which recorded models a restore still has to load.
 * Pure, node-testable.
 *
 * Only models a reload can fetch again are recorded: a stored file, by its key in the scope, and
 * a published asset node, by its node ref. Order is load order.
 */

import {assetSourceName, type NodeRef} from "@/assets/delivery";

export type RecordedModel =
    | {kind: "file"; name: string; streamer?: true}
    | {
          kind: "asset";
          provider: string;
          collection: string;
          subject: string;
          revision: string;
          node?: string;
          /** The name its root row showed, so a restore names it the same. */
          label?: string;
      };

/** Camera position, orbit target and up, minus the recentring offset (`translation`), as the
 *  loading view stores them. */
export interface RecordedCamera {
    v: 1;
    translation: [number, number, number] | null;
    position: [number, number, number];
    target: [number, number, number];
    up: [number, number, number];
}

export interface SceneRecord {
    v: 1;
    models: RecordedModel[];
    camera: RecordedCamera | null;
}

export const EMPTY_SCENE_RECORD: SceneRecord = {v: 1, models: [], camera: null};

const KEY_PREFIX = "ada.sceneRestore:";

export const sceneRecordKey = (scope: string): string => `${KEY_PREFIX}${scope}`;

/** The scene name a recorded model is loaded under. */
export function recordedSourceName(m: RecordedModel): string {
    return m.kind === "file" ? m.name : assetSourceName(assetRef(m));
}

export function assetRef(m: Extract<RecordedModel, {kind: "asset"}>): NodeRef {
    return {
        provider: m.provider,
        collection: m.collection,
        subject: m.subject,
        revision: m.revision,
        ...(m.node && m.node !== m.subject ? {node: m.node} : {}),
    };
}

/** The record entry for a loaded asset node, and the name it was shown under. */
export function recordedAsset(ref: NodeRef, label?: string | null): RecordedModel {
    return {
        kind: "asset",
        provider: ref.provider,
        collection: ref.collection,
        subject: ref.subject,
        revision: ref.revision,
        ...(ref.node && ref.node !== ref.subject ? {node: ref.node} : {}),
        ...(label ? {label} : {}),
    };
}

function isRecord(v: unknown): v is Record<string, unknown> {
    return typeof v === "object" && v !== null && !Array.isArray(v);
}

const nonEmpty = (v: unknown): v is string => typeof v === "string" && v.length > 0;

function isVec3(v: unknown): v is [number, number, number] {
    return Array.isArray(v) && v.length === 3 && v.every((n) => typeof n === "number" && Number.isFinite(n));
}

/** One recorded model, or null when it is not the known shape. */
export function parseRecordedModel(raw: unknown): RecordedModel | null {
    if (!isRecord(raw)) return null;
    if (raw.kind === "file") {
        if (!nonEmpty(raw.name)) return null;
        // A stored file's key, never a URL or a scene-only name.
        if (/^(assets|blob|data|https?):/i.test(raw.name)) return null;
        return raw.streamer === true ? {kind: "file", name: raw.name, streamer: true} : {kind: "file", name: raw.name};
    }
    if (raw.kind === "asset") {
        const {provider, collection, subject, revision, node, label} = raw;
        if (!nonEmpty(provider) || !nonEmpty(collection) || !nonEmpty(subject) || !nonEmpty(revision)) return null;
        if (node !== undefined && node !== null && typeof node !== "string") return null;
        return recordedAsset(
            {provider, collection, subject, revision, ...(nonEmpty(node) ? {node} : {})},
            nonEmpty(label) ? label : null,
        );
    }
    return null;
}

export function parseRecordedCamera(raw: unknown): RecordedCamera | null {
    if (!isRecord(raw) || raw.v !== 1) return null;
    if (!isVec3(raw.position) || !isVec3(raw.target) || !isVec3(raw.up)) return null;
    return {
        v: 1,
        translation: isVec3(raw.translation) ? raw.translation : null,
        position: raw.position,
        target: raw.target,
        up: raw.up,
    };
}

/** The record in `raw`. Never throws: malformed models are dropped, and a model recorded twice
 *  is kept once, at its first place. */
export function parseSceneRecord(raw: string | null): SceneRecord {
    if (!raw) return EMPTY_SCENE_RECORD;
    let doc: unknown;
    try {
        doc = JSON.parse(raw);
    } catch {
        return EMPTY_SCENE_RECORD;
    }
    if (!isRecord(doc) || doc.v !== 1 || !Array.isArray(doc.models)) return EMPTY_SCENE_RECORD;
    let models: RecordedModel[] = [];
    for (const r of doc.models) {
        const m = parseRecordedModel(r);
        if (m) models = withModel(models, m);
    }
    return {v: 1, models, camera: parseRecordedCamera(doc.camera)};
}

export function serialiseSceneRecord(r: SceneRecord): string {
    return JSON.stringify({v: 1, models: r.models, camera: r.camera});
}

/** `models` with `m` appended, unless a model of the same scene name is already there. */
export function withModel(models: readonly RecordedModel[], m: RecordedModel): RecordedModel[] {
    const name = recordedSourceName(m);
    return models.some((x) => recordedSourceName(x) === name) ? [...models] : [...models, m];
}

/** `models` without those loaded under any of `names`. */
export function withoutNames(models: readonly RecordedModel[], names: Iterable<string>): RecordedModel[] {
    const drop = new Set(names);
    return models.filter((m) => !drop.has(recordedSourceName(m)));
}

/** Names in `prev` that `next` no longer has. */
export function removedNames(prev: ReadonlySet<string>, next: ReadonlySet<string>): string[] {
    const out: string[] = [];
    for (const n of prev) if (!next.has(n)) out.push(n);
    return out;
}

/** The recorded models a restore loads, in order: every one not already in the scene (`live`)
 *  and not being loaded by someone else (`skip`). */
export function restorePlan(
    record: SceneRecord,
    live: ReadonlySet<string>,
    skip: ReadonlySet<string> = new Set(),
): RecordedModel[] {
    return record.models.filter((m) => {
        const name = recordedSourceName(m);
        return !live.has(name) && !skip.has(name);
    });
}

type KeyValueStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const defaultStorage = (): KeyValueStorage | undefined => {
    try {
        return globalThis.localStorage;
    } catch {
        return undefined;
    }
};

export function readSceneRecord(scope: string, storage: KeyValueStorage | undefined = defaultStorage()): SceneRecord {
    try {
        return parseSceneRecord(storage?.getItem(sceneRecordKey(scope)) ?? null);
    } catch {
        return EMPTY_SCENE_RECORD;
    }
}

/** Store `r` for `scope`; a record with no models removes the entry. Storage failures are
 *  swallowed. */
export function writeSceneRecord(scope: string, r: SceneRecord, storage: KeyValueStorage | undefined = defaultStorage()): void {
    try {
        if (r.models.length === 0) storage?.removeItem(sceneRecordKey(scope));
        else storage?.setItem(sceneRecordKey(scope), serialiseSceneRecord(r));
    } catch {
        // Private mode or a full store: nothing is restored after a reload.
    }
}

/** Read, change and write back `scope`'s record. */
export function updateSceneRecord(
    scope: string,
    change: (r: SceneRecord) => SceneRecord,
    storage: KeyValueStorage | undefined = defaultStorage(),
): SceneRecord {
    const next = change(readSceneRecord(scope, storage));
    writeSceneRecord(scope, next, storage);
    return next;
}
