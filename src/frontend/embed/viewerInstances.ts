// Several embedded viewers on one page, each with state of its own.
//
// The viewer's state is not per-instance yet: its zustand stores are process
// singletons (see the note in `src/state/AdaViewerContext.tsx`), and the
// imperative pipeline -- the model loader, click picking, the FEA oscillator --
// reads them with `useXStore.getState()` and reaches "the" scene through
// `getViewerRuntime()`. With two viewers on a page that is one set of controls
// for both: the second viewer to mount took over the stores, and every panel's
// buttons then drove the newest viewer's model.
//
// Until the stores are rewritten per-instance, the embed gets isolation at its
// own boundary instead. Exactly one mounted viewer is ACTIVE; the stores hold
// its state and `getViewerRuntime()` answers with its runtime. Every other
// viewer's store state is parked in a snapshot of its own. Turning to a viewer
// -- pointer entry, a press, keyboard focus -- parks the active one's state and
// restores the newcomer's, so a panel's controls always act on the panel they
// sit in, and each keeps its own settings (selection, open panels, mode, scale,
// play state) across switches. A new viewer starts from the stores' initial
// state rather than inheriting the previous one's.
//
// Store work is serialised: a viewer activates for the whole of its model load
// (the loader writes the stores and scene it resolves at call time), and an
// activation that arrives meanwhile waits for the load to finish rather than
// swapping state out from under it.
//
// What stays shared: the stores that persist to the browser (`persist`
// middleware) -- they hold the user's preferences, not a viewer's state -- and
// the ones `AdaViewerContext` already leaves out as process singletons.

import { SINGLETON_VIEWER_STORES } from "../src/state/AdaViewerContext"
import {
    activateViewerRuntime,
    mountViewerRuntime,
    type ViewerRuntime,
} from "../src/state/viewerRuntime"
import {
    getFeaAnimationPhase,
    setFeaAnimationPhase,
} from "../src/utils/scene/fea/feaAnimationDriver"

interface SwappableStore {
    getState(): unknown
    getInitialState(): unknown
    setState(state: unknown, replace: true): void
}

/** The stores whose state is a viewer's own. */
const SWAPPED: [string, SwappableStore][] = (
    Object.entries(SINGLETON_VIEWER_STORES) as [string, unknown][]
)
    .filter(([, hook]) => !(hook as { persist?: unknown }).persist)
    .map(([name, hook]) => [name, hook as SwappableStore])

export interface EmbedInstance {
    readonly runtime: ViewerRuntime
    /** This viewer's store state while it is not the active one. */
    readonly parked: Map<string, unknown>
    /** This viewer's FEA oscillator phase while it is not the active one. */
    feaPhase: number
    disposed: boolean
    /** Told when the viewer becomes, or stops being, the active one. */
    onActiveChange: (active: boolean) => void
    unregister: () => void
}

let active: EmbedInstance | null = null
let queue: Promise<unknown> = Promise.resolve()

/** A new viewer, starting from the stores' initial state. Not yet active. */
export function createEmbedInstance(
    runtime: ViewerRuntime,
    onActiveChange: (active: boolean) => void = () => {},
): EmbedInstance {
    return {
        runtime,
        parked: new Map(SWAPPED.map(([name, store]) => [name, store.getInitialState()])),
        feaPhase: 0,
        disposed: false,
        onActiveChange,
        unregister: mountViewerRuntime(runtime),
    }
}

export function isActiveInstance(inst: EmbedInstance): boolean {
    return active === inst
}

/** The store state `inst` holds while parked, by store name (for its own
 *  render loop, which keeps an inactive viewer's animation running). */
export function parkedState<T>(inst: EmbedInstance, storeName: string): T | undefined {
    return inst.parked.get(storeName) as T | undefined
}

function park(inst: EmbedInstance): void {
    for (const [name, store] of SWAPPED) inst.parked.set(name, store.getState())
    inst.feaPhase = getFeaAnimationPhase()
}

function unpark(inst: EmbedInstance): void {
    for (const [name, store] of SWAPPED) {
        if (inst.parked.has(name)) store.setState(inst.parked.get(name), true)
    }
    setFeaAnimationPhase(inst.feaPhase)
}

function activateNow(inst: EmbedInstance): void {
    if (inst.disposed || active === inst) return
    const previous = active
    if (previous && !previous.disposed) park(previous)
    unpark(inst)
    activateViewerRuntime(inst.runtime)
    active = inst
    previous?.onActiveChange(false)
    inst.onActiveChange(true)
}

/**
 * Run `work` with `inst` active, after any store work already queued. The
 * viewer stays active until `work` settles, and nothing else activates in
 * between -- which is what a model load needs.
 */
export function withActiveInstance<T>(inst: EmbedInstance, work: () => T | Promise<T>): Promise<T> {
    const run = queue.then(() => {
        activateNow(inst)
        return work()
    })
    queue = run.catch(() => undefined)
    return run
}

/** Make `inst` the active viewer, once any store work in flight is done. */
export function requestActivation(inst: EmbedInstance): void {
    void withActiveInstance(inst, () => undefined)
}

/** Forget `inst`. Its parked state is dropped; if it was the active one, the
 *  stores go back to their initial state, so nothing keeps pointing into the
 *  scene being torn down (the FEA session's `mesh`, the selection). */
export function disposeEmbedInstance(inst: EmbedInstance): void {
    if (inst.disposed) return
    inst.disposed = true
    inst.unregister()
    inst.parked.clear()
    if (active === inst) {
        active = null
        for (const [, store] of SWAPPED) store.setState(store.getInitialState(), true)
        setFeaAnimationPhase(0)
    }
}
