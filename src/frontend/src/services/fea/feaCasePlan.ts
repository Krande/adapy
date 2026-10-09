// How a load combination is materialised by the adacpp_fea kernel: the plan
// (one ``combineField`` call per blob of every case field) and the overlay the
// results are described by.
//
// This is the browser twin of the backend's ``artefacts/combine.py``
// (``superpose_case`` + ``write_case``), and is held equal to it byte for byte
// by a test that runs the real wasm module against the numpy reference. The
// arithmetic itself lives in the kernel; what lives here is the WIRING, which
// is where an engine can silently differ:
//
//   * term -> step: each term's basic case is looked up in the field's OWN
//     ``steps`` (``value`` -> ``i``); a term whose basic is not baked there
//     cannot be done from the strides (the server's raw path can);
//   * coefficients: ``combination_steps[].coefficients[k][0]`` -- the float32
//     ``F cos(phi)`` the manifest carries, never recomputed;
//   * derivations: a field's ``derived_components`` become kernel ops, in the
//     manifest's order, args / outputs as column indices. A component derived
//     from ANOTHER field (``field``: a principal stress from the general
//     stress) is computed by combining that source field's blob and writing a
//     NEW layout -- legal only when every component of the field is derived
//     that way, which is the case for every field the bake classifies;
//   * per element type: one call per ``per_type`` bucket, matched to the
//     source field's bucket of the same element type;
//   * ranges: the overlay's ``scalar_range`` is rolled up from the kernel's
//     per-step stats the way the bake's writers record them.
//
// Pure (no DOM, no worker, no wasm): the plan is data, so it is unit tested
// and the worker and the node test run the same code.

import type {
    FeaCaseField,
    FeaCaseOverlay,
    FeaCombinationStep,
    FeaManifest,
    FeaManifestField,
    FeaScalarRange,
} from "../api/fea";

/** The overlay contract this module writes (``LAZY_CASES_VERSION``). */
export const LAZY_CASES_VERSION = 1;
export const CASE_OVERLAY_NAME = "fea.case.json";
export const CASES_PREFIX = "cases/";
/** ``producer.engine`` of a case materialised in the browser. */
export const WASM_ENGINE = "adacpp-wasm";

/** The case cannot be materialised from the baked strides here; the caller
 *  asks the server, which can (raw records) or says why not. */
export class LocalCaseUnsupported extends Error {
    constructor(message: string) {
        super(message);
        this.name = "LocalCaseUnsupported";
    }
}

/** One derivation op as the kernel takes it: column indices. */
export interface KernelOp {
    op: string;
    args: number[];
    out: number[];
}

/** The kernel's ``deriveJson``. ``n_components`` set: a new layout. */
export interface KernelDerive {
    name: string;
    n_components?: number;
    ops: KernelOp[];
}

/** One base blob and the strides of it a plan reads. */
export interface BaseBlobNeed {
    url: string;
    header_bytes: number;
    stride_bytes: number;
    steps: number[];
}

/** One ``combineField`` call: a single-step blob of one bucket of one field. */
export interface KernelJob {
    field: string;
    elemType: string | null;
    /** Base blob the strides are read from (the field's own, or its derivation
     *  source's for a new-layout field), manifest-relative. */
    inUrl: string;
    /** One step index per term, into ``inUrl``. */
    steps: number[];
    /** One float32 coefficient per term (exact: taken from the manifest). */
    factors: number[];
    /** The case blob, relative to the case directory: the base filename. */
    outUrl: string;
    derive: KernelDerive;
    /** The bucket's shape, for the overlay. */
    nElements?: number;
    nIps?: number;
}

export interface CasePlan {
    step: FeaCombinationStep;
    /** ``<n>-<recipeHash8>``. */
    caseDir: string;
    /** The fields the overlay carries, in manifest order. */
    fields: string[];
    jobs: KernelJob[];
    /** Every base stride the jobs read, per blob. */
    needs: BaseBlobNeed[];
}

/** One kernel result's per-step stats (``steps[0]`` of ``combineField``). */
export interface KernelStepStats {
    scalar_range_per_component: Array<[number | null, number | null]>;
    scalar_range_magnitude: [number | null, number | null];
}

export function caseDirName(step: Pick<FeaCombinationStep, "n" | "recipe_hash">): string {
    return `${Math.trunc(step.n)}-${String(step.recipe_hash).slice(0, 8)}`;
}

/** The fields a case has values for: every field with steps that is not a
 *  property (``combine.case_fields``). */
export function caseFields(manifest: Pick<FeaManifest, "fields">): string[] {
    return (manifest.fields ?? [])
        .filter((f) => f.category !== "property" && (f.steps ?? []).length > 0)
        .map((f) => f.name_canonical);
}

function stepIndex(field: FeaManifestField, basic: number): number {
    const s = (field.steps ?? []).find((st) => Number(st.value) === Number(basic));
    if (!s) throw new LocalCaseUnsupported(`field ${field.name_canonical} has no baked step ${basic}`);
    return s.i;
}

function colIndex(components: string[], name: string, what: string): number {
    const i = components.indexOf(name);
    if (i < 0) throw new LocalCaseUnsupported(`${what}: no component ${name}`);
    return i;
}

interface Bucket {
    elemType: string | null;
    blob: {url: string; header_bytes: number; stride_bytes: number};
    nElements?: number;
    nIps?: number;
}

function buckets(field: FeaManifestField): Bucket[] {
    if (field.per_type?.length) {
        return field.per_type.map((pt) => ({
            elemType: pt.elem_type,
            blob: pt.blob,
            nElements: pt.n_elements,
            nIps: pt.n_ips,
        }));
    }
    if (!field.blob) throw new LocalCaseUnsupported(`field ${field.name_canonical} has no blob`);
    return [{elemType: null, blob: field.blob}];
}

/** Plan ``step`` over ``fields`` (default: every case field). Throws
 *  :class:`LocalCaseUnsupported` for anything only the server can do. */
export function planCase(manifest: FeaManifest, step: FeaCombinationStep, fields?: string[]): CasePlan {
    if (step.needs_raw) {
        throw new LocalCaseUnsupported(step.raw_reason || `case ${step.n} needs the raw records`);
    }
    if (manifest.lazy_cases && manifest.lazy_cases.client_tier_a === false) {
        throw new LocalCaseUnsupported("the bake does not offer client-side combinations");
    }
    if (!step.terms?.length || step.coefficients?.length !== step.terms.length) {
        throw new LocalCaseUnsupported(`case ${step.n}: terms and coefficients do not match`);
    }
    const byName = new Map(manifest.fields.map((f) => [f.name_canonical, f]));
    const wanted = fields ?? caseFields(manifest);
    const basics = step.terms.map((t) => t[0]);
    const factors = step.coefficients.map((c) => Math.fround(c[0]));
    const jobs: KernelJob[] = [];
    const needs = new Map<string, BaseBlobNeed>();
    const need = (b: Bucket, steps: number[]) => {
        let n = needs.get(b.blob.url);
        if (!n) {
            n = {url: b.blob.url, header_bytes: b.blob.header_bytes, stride_bytes: b.blob.stride_bytes, steps: []};
            needs.set(b.blob.url, n);
        }
        for (const s of steps) if (!n.steps.includes(s)) n.steps.push(s);
        n.steps.sort((a, b2) => a - b2);
    };

    for (const name of wanted) {
        const field = byName.get(name);
        if (!field) throw new LocalCaseUnsupported(`no field ${name} in the manifest`);
        if (!field.linear_components) {
            throw new LocalCaseUnsupported(`field ${name} has no superposition rule`);
        }
        const comps = field.components;
        const derived = Object.entries(field.derived_components ?? {});
        const sourced = derived.filter(([, r]) => r.field && r.field !== name);
        const ownSteps = basics.map((b) => stepIndex(field, b));

        if (sourced.length === 0) {
            // In place: superpose the field's own strides, re-derive its own columns.
            const ops: KernelOp[] = derived.map(([comp, rule]) => ({
                op: rule.op,
                args: rule.args.map((a) => colIndex(comps, a, name)),
                out: [colIndex(comps, comp, name)],
            }));
            for (const b of buckets(field)) {
                need(b, ownSteps);
                jobs.push({
                    field: name,
                    elemType: b.elemType,
                    inUrl: b.blob.url,
                    steps: ownSteps,
                    factors,
                    outUrl: b.blob.url,
                    derive: {name, ops},
                    nElements: b.nElements,
                    nIps: b.nIps,
                });
            }
            continue;
        }

        // A new layout from one source field: every component must be derived
        // from it (a field mixing its own linear columns with columns derived
        // from another field would need two inputs -- not a field the bake makes).
        const srcName = sourced[0][1].field!;
        if (sourced.length !== comps.length || sourced.some(([, r]) => r.field !== srcName)) {
            throw new LocalCaseUnsupported(`field ${name} mixes own and ${srcName}-derived components`);
        }
        const src = byName.get(srcName);
        if (!src) throw new LocalCaseUnsupported(`derivation source ${srcName} is not in the manifest`);
        const srcSteps = basics.map((b) => stepIndex(src, b));
        const ops: KernelOp[] = sourced.map(([comp, rule]) => ({
            op: rule.op,
            args: rule.args.map((a) => colIndex(src.components, a, srcName)),
            out: [colIndex(comps, comp, name)],
        }));
        const srcBuckets = buckets(src);
        for (const b of buckets(field)) {
            const sb = srcBuckets.find((x) => x.elemType === b.elemType);
            if (!sb) throw new LocalCaseUnsupported(`${name}: derivation source ${srcName} has no ${b.elemType} bucket`);
            const rows = (bb: Bucket, nComp: number) => bb.blob.stride_bytes / (4 * nComp);
            if (rows(sb, src.components.length) !== rows(b, comps.length)) {
                throw new LocalCaseUnsupported(`${name}: derivation source ${srcName} is laid out differently`);
            }
            need(sb, srcSteps);
            jobs.push({
                field: name,
                elemType: b.elemType,
                inUrl: sb.blob.url,
                steps: srcSteps,
                factors,
                outUrl: b.blob.url,
                derive: {name, n_components: comps.length, ops},
                nElements: b.nElements,
                nIps: b.nIps,
            });
        }
    }
    return {step, caseDir: caseDirName(step), fields: wanted, jobs, needs: [...needs.values()]};
}

// ── overlay ─────────────────────────────────────────────────────────────────

/** Python's ``f"{x:g}"`` for the step label (an integral case number in practice). */
export function formatG(x: number): string {
    if (Number.isInteger(x) && Math.abs(x) < 1e6) return String(x);
    let s = x.toPrecision(6);
    if (s.includes("e")) {
        let [m, e] = s.split("e");
        if (m.includes(".")) m = m.replace(/0+$/, "").replace(/\.$/, "");
        const sign = e.startsWith("-") ? "-" : "+";
        const digits = e.replace(/^[+-]/, "").padStart(2, "0");
        return `${m}e${sign}${digits}`;
    }
    if (s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s;
}

function stepEntry(step: FeaCombinationStep) {
    return {
        i: 0,
        value: Number(step.n),
        label: formatG(Number(step.n)),
        ...(step.name ? {name: step.name} : {}),
    };
}

const rng = (lo: number | null, hi: number | null): [number, number] =>
    lo === null || hi === null || !Number.isFinite(lo) || !Number.isFinite(hi) ? [0, 0] : [lo, hi];

function blobPayload(url: string, headerBytes: number, strideBytes: number) {
    return {url, header_bytes: headerBytes, stride_bytes: strideBytes, dtype: "float32", byte_order: "little" as const};
}

/** The ``fea.case.json`` of a plan run: what ``combine.write_case`` writes for
 *  the same case. ``stats`` maps each job's ``outUrl`` to its step stats. */
export function buildCaseOverlay(
    manifest: FeaManifest,
    plan: CasePlan,
    stats: Map<string, KernelStepStats>,
    producer: {engine: string; version?: string; tier?: string},
    prefix: string,
): FeaCaseOverlay {
    const byName = new Map(manifest.fields.map((f) => [f.name_canonical, f]));
    const step = plan.step;
    const fields: FeaCaseField[] = [];
    for (const name of plan.fields) {
        const field = byName.get(name)!;
        const comps = [...field.components];
        const jobs = plan.jobs.filter((j) => j.field === name);
        const perComp = (s: KernelStepStats): Record<string, [number, number]> => {
            const out: Record<string, [number, number]> = {};
            comps.forEach((c, k) => {
                const pair = s.scalar_range_per_component[k] ?? [null, null];
                out[c] = rng(pair[0], pair[1]);
            });
            return out;
        };
        const statsOf = (j: KernelJob) => {
            const s = stats.get(j.outUrl);
            if (!s) throw new Error(`no kernel result for ${j.outUrl}`);
            return s;
        };
        if (!field.per_type?.length) {
            const j = jobs[0];
            const s = statsOf(j);
            const scalar_range: FeaScalarRange = perComp(s);
            if (comps.length >= 2) scalar_range.magnitude = rng(...s.scalar_range_magnitude);
            fields.push({
                name_canonical: name,
                components: comps,
                n_steps: 1,
                steps: [stepEntry(step)],
                blob: blobPayload(j.outUrl, field.blob!.header_bytes, field.blob!.stride_bytes),
                scalar_range,
            });
            continue;
        }
        // The manifest's roll-up across buckets (combine._element_payload).
        const roll: Record<string, [number, number]> = {};
        let mag: [number, number] = [Infinity, -Infinity];
        const perType: NonNullable<FeaCaseField["per_type"]> = [];
        for (const pt of field.per_type) {
            const j = jobs.find((x) => x.elemType === pt.elem_type);
            if (!j) throw new Error(`no job for ${name}/${pt.elem_type}`);
            const s = statsOf(j);
            const sr = perComp(s);
            for (const [c, [lo, hi]] of Object.entries(sr)) {
                const cur = roll[c];
                roll[c] = cur ? [Math.min(cur[0], lo), Math.max(cur[1], hi)] : [lo, hi];
            }
            const [mlo, mhi] = rng(...s.scalar_range_magnitude);
            mag = [Math.min(mag[0], mlo), Math.max(mag[1], mhi)];
            perType.push({
                elem_type: pt.elem_type,
                n_elements: pt.n_elements,
                n_ips: pt.n_ips,
                blob: blobPayload(j.outUrl, pt.blob.header_bytes, pt.blob.stride_bytes),
                scalar_range: sr,
            });
        }
        if (!(mag[0] !== Infinity && mag[1] !== -Infinity)) mag = [0, 0];
        const scalar_range: FeaScalarRange = {...roll};
        if (comps.length >= 2) scalar_range.magnitude = mag;
        fields.push({
            name_canonical: name,
            components: comps,
            n_steps: 1,
            steps: [stepEntry(step)],
            scalar_range,
            per_type: perType,
        });
    }
    // combine.case_overlay's key order: n, name, complex, terms, coefficients, recipe_hash.
    const ordered: Record<string, unknown> = {n: step.n};
    if (step.name) ordered.name = step.name;
    ordered.complex = step.complex;
    ordered.terms = step.terms;
    ordered.coefficients = step.coefficients;
    ordered.recipe_hash = step.recipe_hash;
    const sha = (manifest as {source_sha256?: string}).source_sha256;
    return {
        version: LAZY_CASES_VERSION,
        kind: "fea_case",
        bake_version: manifest.bake_version ?? 4,
        src: manifest.src ?? "",
        ...(sha ? {source_sha256: sha} : {}),
        case: ordered as FeaCaseOverlay["case"],
        producer,
        fields,
        prefix,
    } as FeaCaseOverlay;
}

const NEG_ZERO = "\u0000-0\u0000";

/** ``JSON.stringify`` that keeps a negative zero (``-0.0``, as Python's json
 *  writes it) instead of collapsing it to ``0``: a coefficient or a range
 *  bound of -0 reads back as -0, as it does from the server's copy. */
export function stringifyJson(value: unknown): string {
    const text = JSON.stringify(value, (_k, v) => (typeof v === "number" && Object.is(v, -0) ? NEG_ZERO : v));
    return text.split(JSON.stringify(NEG_ZERO)).join("-0.0");
}

/** Every file a plan writes into the case directory (blobs, then the overlay). */
export function planOutputs(plan: CasePlan): string[] {
    return [...new Set(plan.jobs.map((j) => j.outUrl)), CASE_OVERLAY_NAME];
}
