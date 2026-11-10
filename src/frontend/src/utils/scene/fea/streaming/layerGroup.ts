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
        private readonly make: (positions: Float32Array) => ResultMeshLayer,
    ) {}

    get drawn(): boolean {
        return this.layer !== null;
    }

    show(points: PaintedPoints): void {
        if (points.positions.length === 0) return this.clear();
        if (!this.layer || !sameFloats(this.positions, points.positions)) {
            this.clear();
            this.positions = points.positions;
            this.layer = this.group.add(this.make(points.positions), this.shown);
        }
        this.layer.setColors(points.colors);
        this.layer.setDisplacement(points.displacement);
    }

    setShown(shown: boolean): void {
        this.shown = shown;
        this.group.setShown(this.layer, shown);
    }

    clear(): void {
        this.group.drop(this.layer);
        this.layer = null;
        this.positions = null;
    }
}

function sameFloats(a: Float32Array | null, b: Float32Array): boolean {
    if (!a || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
}
