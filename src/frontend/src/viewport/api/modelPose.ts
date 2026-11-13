// Placing the nodes of a loaded model from outside: a model whose parts are moved by something that
// is still happening (a run that is being followed), where a file's own animation only replays what
// has already been recorded.
//
// The same two layers as the rest of the viewport interface. A viewport answers `modelNodes` with a
// `ModelNodePoser` for a model it has on screen: its adapter, which knows how that viewport holds a
// model (three.js: objects in a scene; the three.js-free one: instances in the renderer). `ModelPose`
// is what a caller holds instead: it outlives the model being loaded, loaded again, and the viewport
// being switched, and puts the nodes back where they were last placed each time.
//
// No three.js in this module.
import {activeViewport, onActiveViewportChange, onModelLoaded, type ViewportApi} from "./viewportApi";

/** Sixteen numbers per node: its place in the model's own coordinates (those of the file), column-major. */
export const POSE_MATRIX_SIZE = 16;

/** A viewport's adapter for placing named nodes of one model it has on screen. */
export interface ModelNodePoser {
    /** Per name asked for, in that order: whether the model has a node of that name to place. */
    readonly found: readonly boolean[];
    /** False once the model is no longer on screen: ask the viewport again. */
    alive(): boolean;
    /**
     * Put each named node where its matrix says: `POSE_MATRIX_SIZE` numbers per name, in the order
     * the names were asked for. What hangs on a node (its children in the file) moves with it. A
     * matrix of zeros hides the node. Names that were not found are skipped. Allocates nothing.
     */
    place(matrices: Float32Array): void;
}

/** A model's named nodes as a caller places them, whichever viewport is on screen. */
export interface ModelPose {
    /** Place them (see `ModelNodePoser.place`). Kept: a model that arrives later is placed so. */
    place(matrices: Float32Array): void;
    /** Per name: whether the model on screen has that node; null while there is no such model. */
    found(): readonly boolean[] | null;
    /** Stop keeping it in place. The model stays as it was last placed. */
    remove(): void;
}

class Pose implements ModelPose {
    private poser: ModelNodePoser | null = null;
    private viewport: ViewportApi | null = null;
    private last: Float32Array | null = null;

    constructor(
        private readonly sourceName: string,
        private readonly names: readonly string[],
    ) {}

    private acquire(): ModelNodePoser | null {
        const viewport = activeViewport();
        if (this.poser && viewport === this.viewport && this.poser.alive()) return this.poser;
        this.viewport = viewport;
        this.poser = viewport?.modelNodes(this.sourceName, this.names) ?? null;
        return this.poser;
    }

    place(matrices: Float32Array): void {
        const size = this.names.length * POSE_MATRIX_SIZE;
        if (!this.last || this.last.length !== size) this.last = new Float32Array(size);
        this.last.set(matrices.length > size ? matrices.subarray(0, size) : matrices);
        this.acquire()?.place(this.last);
    }

    found(): readonly boolean[] | null {
        return this.acquire()?.found ?? null;
    }

    /** The models on screen may have changed: a model that is new here is placed as the last one was. */
    refresh(): void {
        const before = this.poser;
        const now = this.acquire();
        if (now && now !== before && this.last) now.place(this.last);
    }

    remove(): void {
        poses.delete(this);
        if (poses.size === 0) unbind();
    }
}

const poses = new Set<Pose>();
let unbinders: (() => void)[] = [];

function unbind(): void {
    for (const off of unbinders) off();
    unbinders = [];
}

/** Look at every pose again: called when a model was loaded or the viewport on screen changed. */
export function refreshModelPoses(): void {
    for (const pose of poses) pose.refresh();
}

/**
 * The nodes called `names` of the model loaded as `sourceName`, to be placed by the caller. The
 * model need not be on screen yet. On the three.js-free viewport a model's nodes can only be placed
 * when it was loaded as `posable` (see `LoadModelSource.posable`).
 */
export function createModelPose(sourceName: string, names: readonly string[]): ModelPose {
    const pose = new Pose(sourceName, [...names]);
    if (poses.size === 0) unbinders = [onActiveViewportChange(refreshModelPoses), onModelLoaded(refreshModelPoses)];
    poses.add(pose);
    return pose;
}
