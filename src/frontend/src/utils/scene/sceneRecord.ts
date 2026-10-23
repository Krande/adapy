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

/**
 * What was hidden and selected in one recorded model, by element id (the draw-range key, stable
 * across reloads of the same model) -- never by scene object, which a reload replaces.
 *
 * Visibility is stored whichever way is shorter: `hidden` lists the hidden elements, or `shown`
 * lists the only ones left drawn (an isolation, where the hidden list would be nearly the model).
 */
export interface RecordedSourceView {
    hidden?: string[];
    shown?: string[];
    selected?: string[];
}

/** Hidden / selected elements per recorded model, keyed by the model's scene name. */
export interface RecordedView {
    v: 1;
    sources: Record<string, RecordedSourceView>;
}

export interface SceneRecord {
    v: 1;
    models: RecordedModel[];
    camera: RecordedCamera | null;
    /** Absent in records written before visibility and selection were kept. */
    view?: RecordedView | null;
}

export const EMPTY_SCENE_RECORD: SceneRecord = {v: 1, models: [], camera: null};

/** The longest serialised view a record keeps. The record shares the origin's localStorage (some
 *  5 MB, in UTF-16) with everything else; past this the selection is dropped first, then the view. */
export const MAX_VIEW_CHARS = 1_000_000;

/** One model's visibility, in its shorter form: the hidden elements, or -- when more are hidden
 *  than drawn -- the drawn ones. Null when nothing is hidden. */
export function compactVisibility(hidden: readonly string[], shown: readonly string[]): Pick<RecordedSourceView, "hidden" | "shown"> | null {
    if (hidden.length === 0) return null;
    return shown.length < hidden.length ? {shown: [...shown].sort()} : {hidden: [...hidden].sort()};
}

/** `view` cut to fit `limit` characters: whole, without the selection, or not at all (null). */
export function fitView(view: RecordedView | null, limit = MAX_VIEW_CHARS): RecordedView | null {
    if (!view || Object.keys(view.sources).length === 0) return null;
    if (JSON.stringify(view).length <= limit) return view;
    const sources: Record<string, RecordedSourceView> = {};
    for (const [name, s] of Object.entries(view.sources)) {
        const lean: RecordedSourceView = {};
        if (s.shown) lean.shown = s.shown;
        else if (s.hidden) lean.hidden = s.hidden;
        if (lean.hidden || lean.shown) sources[name] = lean;
    }
    const lean: RecordedView = {v: 1, sources};
    if (Object.keys(sources).length && JSON.stringify(lean).length <= limit) return lean;
    return null;
}

/** What re-applying one model's recorded view does to each of its meshes (given by the element
 *  ids each one draws): which ranges to hide, or which to keep under an isolation, and which to
 *  select. Ids no mesh draws any more are counted, not applied. */
export function planSourceView(
    view: RecordedSourceView,
    meshes: readonly ReadonlySet<string>[],
): {
    perMesh: {hide: string[]; keep: Set<string> | null; select: string[]}[];
    missing: {hidden: number; selected: number};
} {
    const drawn = (id: string) => meshes.some((ids) => ids.has(id));
    const visibility = view.shown ?? view.hidden ?? [];
    const missing = {
        hidden: visibility.filter((id) => !drawn(id)).length,
        selected: (view.selected ?? []).filter((id) => !drawn(id)).length,
    };
    const shown = view.shown ? new Set(view.shown) : null;
    const perMesh = meshes.map((ids) => ({
        hide: view.hidden ? view.hidden.filter((id) => ids.has(id)) : [],
        keep: shown ? new Set([...ids].filter((id) => shown.has(id))) : null,
        // A selected element left hidden by the view is not selected (hidden is never selected).
        select: (view.selected ?? []).filter((id) => ids.has(id)),
    }));
    return {perMesh, missing};
}

/** The one short note a restore leaves about what it could not find, or null. */
export function missingNote(missing: {hidden: number; selected: number}): string | null {
    const parts: string[] = [];
    const n = (k: number, what: string) => `${k} ${what} object${k === 1 ? "" : "s"}`;
    if (missing.hidden) parts.push(n(missing.hidden, "hidden"));
    if (missing.selected) parts.push(n(missing.selected, "selected"));
    if (!parts.length) return null;
    const one = parts.length === 1 && missing.hidden + missing.selected === 1;
    return `${parts.join(" and ")} ${one ? "was" : "were"} not found`;
}

const stringList = (v: unknown): string[] | undefined =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.length > 0) : undefined;

/** The view in `raw`, or null when it is missing or not the known shape. */
export function parseRecordedView(raw: unknown): RecordedView | null {
    if (!isRecord(raw) || raw.v !== 1 || !isRecord(raw.sources)) return null;
    const sources: Record<string, RecordedSourceView> = {};
    for (const [name, s] of Object.entries(raw.sources)) {
        if (!name || !isRecord(s)) continue;
        const hidden = stringList(s.hidden);
        const shown = stringList(s.shown);
        const selected = stringList(s.selected);
        const out: RecordedSourceView = {};
        // `shown` is an isolation and wins: both at once would contradict each other.
        if (shown) out.shown = shown;
        else if (hidden?.length) out.hidden = hidden;
        if (selected?.length) out.selected = selected;
        if (out.shown || out.hidden || out.selected) sources[name] = out;
    }
    return Object.keys(sources).length ? {v: 1, sources} : null;
}

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
    const view = parseRecordedView(doc.view);
    return {v: 1, models, camera: parseRecordedCamera(doc.camera), ...(view ? {view} : {})};
}

export function serialiseSceneRecord(r: SceneRecord): string {
    const view = fitView(r.view ?? null);
    return JSON.stringify({v: 1, models: r.models, camera: r.camera, ...(view ? {view} : {})});
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
