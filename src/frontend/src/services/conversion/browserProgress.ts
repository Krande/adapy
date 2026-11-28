// Progress of an in-browser conversion, told through the same job entry (and so the same
// toast row and "Loading N%" bar) a worker conversion's progress is.
//
// One bar, four bands:
//   fetch    the source bytes are read from storage           (0 .. 0.05)
//   setup    the Pyodide runtime boots / installs its stacks   (0.05 .. 0.2)
//   convert  adapy's own (stage, fraction) reports             (0.2 .. 0.88)
//   upload   the result goes back to storage, then "ready"     (0.9 .. 1)
//
// The setup band moves on each distinct log line the worker posts while it boots or installs
// (loading Pyodide, numpy, the adapy / adacpp wheels, a plugin's dependencies): each one
// closes a quarter of what is left of the band, so the bar advances per stage without
// claiming to know how many stages there are. The convert band maps adapy's 0..1
// (ada.cadit.wasm_convert's on_progress) linearly. The bar never goes backwards, and the
// store is written at most once per `minIntervalMs` (the last held-back update is written
// when the interval runs out, so the final state of a burst is never lost).

import {useConversionStore, type ConversionJob} from "@/state/conversionStore";

export const BROWSER_BANDS = {
    fetch: 0.02,
    setup: [0.05, 0.2] as const,
    convert: [0.2, 0.88] as const,
    upload: 0.9,
};

/** Appended to the stages the browser engine reports, as the worker's stages carry none. */
export const BROWSER_HINT = " (browser)";

export interface ProgressPatch {
    progress: number;
    stage: string;
}

export interface BrowserProgressOptions {
    /** Least time between two store writes (default 100 ms: at most 10 per second). */
    minIntervalMs?: number;
    now?: () => number;
    setTimer?: (fn: () => void, ms: number) => unknown;
    clearTimer?: (handle: unknown) => void;
}

export interface BrowserProgress {
    /** A stage of the pipeline itself (fetching, uploading), written now, at `fraction` or above. */
    stage(stage: string, fraction: number): void;
    /** A worker log line while the runtime boots or installs; ignored once adapy reports. */
    setupLog(message: string): void;
    /** adapy's report: `fraction` (0..1) of the conversion itself. */
    convert(stage: string, fraction: number): void;
    /** Drop a held-back update and stop the timer (the job is about to be finished). */
    dispose(): void;
    /** The furthest the bar has been. */
    readonly progress: number;
}

const clamp01 = (x: number) => (Number.isFinite(x) ? Math.min(1, Math.max(0, x)) : 0);

/** The progress state machine over a `write` sink (the store in the pipelines, a list in tests). */
export function createBrowserProgress(
    write: (patch: ProgressPatch) => void,
    opts: BrowserProgressOptions = {},
): BrowserProgress {
    const interval = opts.minIntervalMs ?? 100;
    const now = opts.now ?? (() => Date.now());
    const setTimer = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    const clearTimer = opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
    let last = 0;
    let lastWrite = Number.NEGATIVE_INFINITY;
    let pending: ProgressPatch | null = null;
    let timer: unknown = null;
    let converting = false;
    let setupAt: number = BROWSER_BANDS.setup[0];
    let lastSetupMessage = "";
    let disposed = false;

    const stopTimer = () => {
        if (timer !== null) clearTimer(timer);
        timer = null;
    };
    const emit = (patch: ProgressPatch) => {
        pending = null;
        stopTimer();
        lastWrite = now();
        write(patch);
    };
    const push = (fraction: number, stage: string, force: boolean) => {
        if (disposed) return;
        last = Math.max(last, clamp01(fraction));
        const patch = {progress: last, stage};
        const wait = interval - (now() - lastWrite);
        if (force || wait <= 0) {
            emit(patch);
            return;
        }
        pending = patch;
        if (timer === null) {
            timer = setTimer(() => {
                timer = null;
                if (pending && !disposed) emit(pending);
            }, wait);
        }
    };

    return {
        stage(stage, fraction) {
            push(fraction, stage, true);
        },
        setupLog(message) {
            const msg = String(message || "").trim();
            if (converting || !msg || msg === lastSetupMessage) return;
            lastSetupMessage = msg;
            const [, hi] = BROWSER_BANDS.setup;
            setupAt += (hi - setupAt) * 0.25;
            push(setupAt, `${msg}${BROWSER_HINT}`, false);
        },
        convert(stage, fraction) {
            converting = true;
            const [lo, hi] = BROWSER_BANDS.convert;
            const f = clamp01(fraction);
            // Written as a blend so a finished conversion lands on `hi` exactly.
            push(lo * (1 - f) + hi * f, `${stage}${BROWSER_HINT}`, false);
        },
        dispose() {
            disposed = true;
            pending = null;
            stopTimer();
        },
        get progress() {
            return last;
        },
    };
}

/** A BrowserProgress writing into the conversion store's entry `storeKey` (seeded with `job`).
 * Writes only while that entry is still running: a dismissed or finished job is not revived. */
export function browserJobProgress(storeKey: string, job: ConversionJob, opts?: BrowserProgressOptions): BrowserProgress {
    return createBrowserProgress((patch) => {
        const store = useConversionStore.getState();
        const current = store.jobs[storeKey];
        if (!current || (current.status !== "running" && current.status !== "queued")) return;
        store.setJob(storeKey, {...current, ...patch});
    }, opts);
}

/** The job's last entry in the store, or `job` when it has none (to finish it from). */
export function currentJob(storeKey: string, job: ConversionJob): ConversionJob {
    return useConversionStore.getState().jobs[storeKey] || job;
}
