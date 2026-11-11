// FEA streaming: the layers of one result, as one.
//
// Owns: what a result drawn through result layers (viewport/api/resultLayer.ts) is made of on the
// viewport -- the faces, the element edges, and whatever else moves with them -- held as one layer
// the controls and the playback driver scale, while each member can be shown, hidden or replaced
// on its own. Also the layers whose points are worked out anew by each paint (result-point
// markers, the coloured lines of line elements).
//
// No three.js in this module or in anything it imports.

import type {ResultMeshLayer} from "@/viewport/api/resultLayer";
import type {PaintedPoints} from "../elemFieldPaint";

/** Layers that move as one. The deformation scale goes to every member; a member is drawn while
 *  the group is and the member itself is wanted. */
export class ResultGroup implements ResultMeshLayer {
    /** Each member, and whether it is wanted on screen. */
    private readonly members = new Map<ResultMeshLayer, boolean>();
    private influence = 0;
    private visible = true;
    /** The member that carries the result's colours, when there is one. */
    faces: ResultMeshLayer | null = null;
    /** Where a displacement in the mesh's own node numbering goes: each member takes it its own
     *  way (the session knows which). */
    onDisplacement: ((xyz: Float32Array) => void) | null = null;

    get vertexCount(): number {
        return this.faces?.vertexCount ?? 0;
    }

    /** How much of the displacement is drawn now. */
    get scale(): number {
        return this.influence;
    }

    add<T extends ResultMeshLayer>(layer: T, shown = true): T {
        this.members.set(layer, shown);
        layer.setInfluence(this.influence);
        layer.setVisible(this.visible && shown);
        return layer;
    }

    /** Take a member off the viewport. */
    drop(layer: ResultMeshLayer | null): void {
        if (!layer || !this.members.delete(layer)) return;
        if (this.faces === layer) this.faces = null;
        layer.remove();
    }

    setShown(layer: ResultMeshLayer | null, shown: boolean): void {
        if (!layer || this.members.get(layer) === shown || !this.members.has(layer)) return;
        this.members.set(layer, shown);
        layer.setVisible(this.visible && shown);
    }

    setColors(rgb: Float32Array): void {
        this.faces?.setColors(rgb);
    }

    setDisplacement(xyz: Float32Array): void {
        this.onDisplacement?.(xyz);
    }

    setInfluence(influence: number): void {
        this.influence = influence;
        for (const layer of this.members.keys()) layer.setInfluence(influence);
    }

    setColorsVisible(visible: boolean): void {
        this.faces?.setColorsVisible(visible);
    }

    setColor(rgb: readonly [number, number, number] | null): void {
        this.faces?.setColor(rgb);
    }

    setOpacity(opacity: number): void {
        this.faces?.setOpacity(opacity);
    }

    setVisible(visible: boolean): void {
        this.visible = visible;
        for (const [layer, shown] of this.members) layer.setVisible(visible && shown);
    }

    remove(): void {
        for (const layer of this.members.keys()) layer.remove();
        this.members.clear();
        this.faces = null;
    }
}

/** A member of a group whose points each paint works out anew. While the points stay where they
 *  were (stepping through a field, playing it) only their colours and displacement are replaced;
 *  a paint that moves or adds points makes the layer again. */
export class PaintedPointsLayer {
    private layer: ResultMeshLayer | null = null;
    private positions: Float32Array | null = null;
    private shown = true;

    constructor(
        private readonly group: ResultGroup,
        private readonly make: (points: PaintedPoints) => ResultMeshLayer,
    ) {}

    get drawn(): boolean {
        return this.layer !== null;
    }

    /** Draw `points`. True when the layer was made again or taken away. */
    show(points: PaintedPoints): boolean {
        if (points.positions.length === 0) return this.clear();
        let made = false;
        if (!this.layer || !(this.positions === points.positions || sameFloats(this.positions, points.positions))) {
            // The new layer first: what both draw stays on the viewport throughout, hidden and
            // selected as it was.
            const before = this.layer;
            this.layer = this.group.add(this.make(points), this.shown);
            this.group.drop(before);
            made = true;
        }
        // The same places, so the array to compare the next ones with by identity first.
        this.positions = points.positions;
        this.layer.setColors(points.colors);
        this.layer.setDisplacement(points.displacement);
        return made;
    }

    setShown(shown: boolean): void {
        this.shown = shown;
        this.group.setShown(this.layer, shown);
    }

    /** Take the layer away. True when there was one. */
    clear(): boolean {
        const had = this.layer !== null;
        this.group.drop(this.layer);
        this.layer = null;
        this.positions = null;
        return had;
    }
}

function sameFloats(a: Float32Array | null, b: Float32Array): boolean {
    if (!a || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
}
