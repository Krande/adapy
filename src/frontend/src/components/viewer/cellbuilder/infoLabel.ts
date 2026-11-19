/**
 * The builder's INFO LABEL: what the selection is, beside it on screen, while the
 * builder's `infoLabel` view flag is on (I).
 *
 * Owns: the lines the label shows for a pick (`infoLines`, pure: a space's three
 * dimensions, an equipment's size, masses and centre of gravity, an opening's size, depth
 * and side, a plugin's ref as its provider words it) and the one sprite that draws them,
 * in the numeric readout's style. The sprite sits in the builder's container, so it is
 * hidden with the builder's layer (a shell's mode that does not show the cells), and it is
 * also hidden while the cells are switched off, the flag is off or nothing is picked.
 */

import * as THREE from "three";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import type {BuilderRef} from "@/state/cellbuilder/refsSlice";
import type {BuilderSelection} from "@/state/cellbuilder/types";
import {OPENING_SIDES, type OpeningSide} from "@/state/cellbuilder/docCodec";
import {requestRender} from "@/state/perfStore";
import type {CellBox, Vec3} from "@/utils/cellbuilder/snap";
import {formatLength} from "./cellDimensions";
import type {CellBuilderScene} from "./sceneContext";

const AXES = ["X", "Y", "Z"] as const;

const num = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v)
        ? v
        : typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))
          ? Number(v)
          : null;

/** "1 200 kg": whole kilograms, thousands apart. */
const kg = (v: number): string => `${Math.round(v).toLocaleString("en-US").replace(/,/g, " ")} kg`;
const dims = (size: readonly number[]): string => size.map(formatLength).join(" × ");

/** The axes of an opening's side: `n` through it, `u` / `v` its width and height (docCodec's sideAxes). */
function openingAxes(cell: BuilderCell): {n: 0 | 1 | 2; u: 0 | 1 | 2; v: 0 | 1 | 2; side: OpeningSide | null} {
    const side = OPENING_SIDES.includes(cell.params?.SPACE_SIDE as OpeningSide) ? (cell.params.SPACE_SIDE as OpeningSide) : null;
    // A global box names no side: its thinnest axis is the one it cuts through.
    const n: 0 | 1 | 2 = side
        ? side.endsWith("Z") ? 2 : side.endsWith("Y") ? 1 : 0
        : (([0, 1, 2] as const).reduce((a, b) => (cell.size[b] < cell.size[a] ? b : a)));
    const [u, v] = ([0, 1, 2] as const).filter((a) => a !== n) as [0 | 1 | 2, 0 | 1 | 2];
    return {n, u, v, side};
}

function cellLines(cell: BuilderCell): string[] {
    if (cell.kind === "cell") {
        // The resize readout's three dimensions, one per line.
        return [`Space ${cell.name}`, ...AXES.map((a, i) => `${a} ${formatLength(cell.size[i])}`)];
    }
    if (cell.kind === "equipment") {
        const p = cell.params ?? {};
        const out = [`${cell.equipmentType ?? "Equipment"} ${cell.name}`, `L×W×H ${dims(cell.size)}`];
        const dry = num(p.massDry);
        const cont = num(p.massCont);
        if (dry !== null) out.push(cont ? `Mass ${kg(dry)} dry + ${kg(cont)} contents` : `Mass ${kg(dry)} dry`);
        else if (cont) out.push(`Mass ${kg(cont)} contents`);
        const cog = [num(p.COGx), num(p.COGy), num(p.COGz)];
        if (cog.some((c) => c !== null)) out.push(`COG (${cog.map((c) => formatLength(c ?? 0)).join(", ")})`);
        return out;
    }
    if (cell.kind === "opening") {
        const {n, u, v, side} = openingAxes(cell);
        const out = [
            `${cell.subtype ? cell.subtype[0].toUpperCase() + cell.subtype.slice(1) : "Opening"} ${cell.name}`,
            `W×H ${formatLength(cell.size[u])} × ${formatLength(cell.size[v])}`,
            `Depth ${formatLength(cell.size[n])}`,
        ];
        if (side) out.push(`Side ${side}`);
        return out;
    }
    if (cell.kind === "loft" && cell.loft) return [`Loft ${cell.loft.member}`, `Bay ${cell.loft.bay + 1} of ${cell.loft.bandCount}`];
    return [cell.name];
}

/**
 * The info label's lines for `selection`, or none (nothing picked, or a pick that is gone).
 * A ref shows what its provider says of it (`BuilderRef.info`), under its kind and name;
 * without that, its size.
 */
export function infoLines(
    selection: BuilderSelection | null,
    state: {cells: Readonly<Record<string, BuilderCell>>; refs: Readonly<Record<string, BuilderRef>>},
): string[] {
    if (!selection) return [];
    if (selection.kind === "ref") {
        const ref = state.refs[selection.refId];
        if (!ref) return [];
        const head = `${ref.kindLabel} ${ref.name}`;
        return ref.info?.length ? [head, ...ref.info] : [head, dims(ref.size)];
    }
    const cell = state.cells[selection.cellId];
    return cell ? cellLines(cell) : [];
}

/** The box the label sits above, in the model frame. */
function pickedBox(selection: BuilderSelection | null, state: {cells: Record<string, BuilderCell>; refs: Record<string, BuilderRef>}): CellBox | null {
    if (!selection) return null;
    if (selection.kind === "ref") return state.refs[selection.refId] ?? null;
    return state.cells[selection.cellId] ?? null;
}

const LINE_H = 30;
const PAD = 10;
const WIDTH = 384;
/** Screen size per canvas pixel, with `sizeAttenuation` off: the numeric readout's. */
const SCALE_PER_PX = 0.17 / 256;

/** Draw `lines` on the label's canvas: the readout's dark plate, the heading brighter. */
function drawLines(canvas: HTMLCanvasElement, lines: readonly string[]): void {
    canvas.width = WIDTH;
    canvas.height = PAD * 2 + LINE_H * lines.length;
    const g = canvas.getContext("2d");
    // No 2D context (a canvas the browser refuses): an empty plate beats a thrown render.
    if (!g) return;
    g.clearRect(0, 0, canvas.width, canvas.height);
    g.fillStyle = "rgba(17,24,39,0.86)";
    g.beginPath();
    g.roundRect?.(1, 1, canvas.width - 2, canvas.height - 2, 10);
    g.fill();
    g.textBaseline = "middle";
    g.textAlign = "left";
    lines.forEach((line, i) => {
        g.font = i === 0 ? "bold 22px ui-sans-serif, system-ui, sans-serif" : "20px ui-monospace, monospace";
        g.fillStyle = i === 0 ? "#f3f4f6" : "#22c55e";
        g.fillText(line, PAD + 4, PAD + LINE_H * i + LINE_H / 2, WIDTH - PAD * 2);
    });
}

/** The label's sprite, made the first time it is shown. */
function labelSprite(ctx: CellBuilderScene): THREE.Sprite {
    if (ctx.infoLabel) return ctx.infoLabel;
    const canvas = document.createElement("canvas");
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(
        new THREE.SpriteMaterial({map: tex, depthTest: false, transparent: true, sizeAttenuation: false, toneMapped: false}),
    );
    // Its bottom edge at the anchor, so it stands above the box and never covers it.
    sprite.center.set(0.5, 0);
    sprite.renderOrder = 8;
    sprite.userData.__excludeFromFit = true;
    sprite.visible = false;
    ctx.container.add(sprite);
    ctx.infoLabel = sprite;
    return sprite;
}

/** Where the label goes: above the middle of the box's top. */
const anchorOf = (box: CellBox): Vec3 => [box.origin[0] + box.size[0] / 2, box.origin[1] + box.size[1] / 2, box.origin[2] + box.size[2]];

/** The label on screen, of whichever builder scene drew it last. */
let shownLabel: THREE.Sprite | null = null;

/** Show, move or hide the label for the store as it is now. */
export function syncInfoLabel(ctx: CellBuilderScene): void {
    const st = useCellBuilderStore.getState();
    const lines = st.active && st.infoLabel && st.cellsVisible ? infoLines(st.selection, st) : [];
    const box = lines.length ? pickedBox(st.selection, st) : null;
    if (!box) {
        if (ctx.infoLabel?.visible) {
            ctx.infoLabel.visible = false;
            requestRender();
        }
        return;
    }
    const sprite = labelSprite(ctx);
    const key = lines.join("\n");
    const material = sprite.material as THREE.SpriteMaterial;
    const tex = material.map as THREE.CanvasTexture;
    if (sprite.userData.text !== key) {
        // A new canvas and texture for new text, never the old one redrawn: its height follows the
        // number of lines, and a renderer that keeps a texture by its id and updates it in place
        // (the adacpp one) drew the new lines over what was left of the previous pick's label.
        const canvas = document.createElement("canvas");
        drawLines(canvas, lines);
        const next = new THREE.CanvasTexture(canvas);
        next.colorSpace = THREE.SRGBColorSpace;
        material.map = next;
        material.needsUpdate = true;
        tex?.dispose();
        sprite.userData.text = key;
        sprite.scale.set(canvas.width * SCALE_PER_PX, canvas.height * SCALE_PER_PX, 1);
    }
    sprite.position.set(...anchorOf(box));
    sprite.visible = true;
    // One label on screen, the current pick's: another builder scene's (a viewer mounted again, a
    // second viewport) goes when this one shows.
    if (shownLabel && shownLabel !== sprite) shownLabel.visible = false;
    shownLabel = sprite;
    requestRender();
}

/** Free the label (the builder's scene is going). */
export function disposeInfoLabel(ctx: CellBuilderScene): void {
    const sprite = ctx.infoLabel;
    if (!sprite) return;
    const material = sprite.material as THREE.SpriteMaterial;
    material.map?.dispose();
    material.dispose();
    ctx.container.remove(sprite);
    ctx.infoLabel = null;
    if (shownLabel === sprite) shownLabel = null;
}
