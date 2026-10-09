// Export the active FEA animation as an MP4 or a GIF.
//
// Deterministic, not a screen recording: every frame of the plan is applied (a
// step fetch for a time history, a deformation factor otherwise) and rendered
// once by the viewer's own renderer at the EXPORT resolution -- the drawing
// buffer is resized for the duration (the on-screen canvas keeps its CSS size,
// and the render loop is suspended so the live view never draws into it) and a
// camera clone takes the export's aspect. Copying off the real canvas keeps the
// colours, lighting and antialiasing on screen; a render target would not (three
// renders those in linear colour). The legend (field, unit, time/case, colour
// bar) and the orientation gizmo are burned in when asked for.
//
// The encoders are imported on demand, so they cost nothing until an export.

import * as THREE from "three";

import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {setRenderSuspended} from "@/state/perfStore";
import {getColormap} from "@/utils/scene/fea/colormaps";
import {selectedResultUnit} from "@/utils/scene/fea/resultUnits";
import {formatStepTime} from "@/utils/scene/fea/timeHistory";
import {mergeCaseSteps} from "@/utils/scene/fea/caseSteps";

import {
    exportFileName,
    normaliseExportSettings,
    planExportFrames,
    resolveExportSize,
    type ExportFormat,
    type ExportFrame,
    type ExportSettings,
} from "./framePlan";
import type {FrameSink} from "./frameSink";

export type AnimationFormat = ExportFormat;

export interface ExportProgress {
    done: number;
    total: number;
}

export interface ExportOptions {
    settings: Partial<ExportSettings>;
    onProgress?: (p: ExportProgress) => void;
    signal?: AbortSignal;
}

async function createGifSink(canvas: HTMLCanvasElement, fps: number): Promise<FrameSink> {
    const {GIFEncoder, quantize, applyPalette} = await import("gifenc");
    const ctx = canvas.getContext("2d", {willReadFrequently: true})!;
    const gif = GIFEncoder();
    const delay = Math.round(1000 / fps);
    return {
        add: async (index) => {
            const {data} = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const palette = quantize(data, 256);
            gif.writeFrame(applyPalette(data, palette), canvas.width, canvas.height, {
                palette,
                delay,
                // Loop forever; only the first frame carries the application extension.
                repeat: index === 0 ? 0 : undefined,
            });
        },
        finish: async () => {
            gif.finish();
            return new Blob([gif.bytes()], {type: "image/gif"});
        },
        cancel: async () => {},
    };
}

/** CSS colour behind the canvas, used when the scene itself is transparent. */
function backgroundFill(renderer: THREE.WebGLRenderer, scene: THREE.Scene): string {
    if (scene.background instanceof THREE.Color) return `#${scene.background.getHexString()}`;
    if (renderer.getClearAlpha() > 0) {
        const c = new THREE.Color();
        renderer.getClearColor(c);
        return `#${c.getHexString()}`;
    }
    let el: HTMLElement | null = renderer.domElement.parentElement;
    while (el) {
        const bg = getComputedStyle(el).backgroundColor;
        if (bg && bg !== "transparent" && !bg.startsWith("rgba(0, 0, 0, 0")) return bg;
        el = el.parentElement;
    }
    return "#ffffff";
}

interface LegendInfo {
    title: string;
    subtitle: string;
    min: number;
    max: number;
    colormap: string;
    showBar: boolean;
    timeHistory: boolean;
}

function fmt(value: number): string {
    if (!Number.isFinite(value)) return "—";
    const m = Math.abs(value);
    if ((m !== 0 && m < 1e-3) || m >= 1e6) return value.toExponential(3);
    return value.toLocaleString(undefined, {maximumSignificantDigits: 4});
}

/** Field, unit, time and colour bar in the bottom-left corner. */
function drawOverlay(ctx: CanvasRenderingContext2D, w: number, h: number, info: LegendInfo, frame: ExportFrame) {
    const scale = Math.max(0.6, Math.min(w, h) / 720);
    const pad = Math.round(12 * scale);
    const font = Math.round(14 * scale);
    const barW = Math.round(220 * scale);
    const barH = Math.round(12 * scale);
    const lines = [info.title, info.subtitle, info.timeHistory ? `t = ${frame.label} s` : `Case: ${frame.label}`].filter(
        (l) => l.length > 0,
    );
    ctx.font = `600 ${font}px system-ui, sans-serif`;
    const textW = Math.max(barW, ...lines.map((l) => ctx.measureText(l).width));
    const boxW = textW + 2 * pad;
    const boxH = pad + lines.length * (font + 4) + (info.showBar ? barH + font + 10 : 0) + pad;
    const x0 = pad;
    const y0 = h - boxH - pad;

    ctx.fillStyle = "rgba(255, 255, 255, 0.82)";
    ctx.fillRect(x0, y0, boxW, boxH);
    ctx.fillStyle = "#111";
    ctx.textBaseline = "top";
    let y = y0 + pad;
    lines.forEach((line, i) => {
        ctx.font = `${i === 0 ? 600 : 400} ${font}px system-ui, sans-serif`;
        ctx.fillText(line, x0 + pad, y);
        y += font + 4;
    });
    if (info.showBar) {
        const map = getColormap(info.colormap);
        const rgb = new Float32Array(3);
        const grad = ctx.createLinearGradient(x0 + pad, 0, x0 + pad + barW, 0);
        for (let i = 0; i <= 10; i++) {
            map(i / 10, rgb);
            grad.addColorStop(i / 10, `rgb(${Math.round(rgb[0] * 255)}, ${Math.round(rgb[1] * 255)}, ${Math.round(rgb[2] * 255)})`);
        }
        y += 4;
        ctx.fillStyle = grad;
        ctx.fillRect(x0 + pad, y, barW, barH);
        ctx.fillStyle = "#111";
        ctx.font = `400 ${font}px ui-monospace, monospace`;
        y += barH + 4;
        ctx.textAlign = "left";
        ctx.fillText(fmt(info.min), x0 + pad, y);
        ctx.textAlign = "right";
        ctx.fillText(fmt(info.max), x0 + pad + barW, y);
        ctx.textAlign = "left";
    }
}

function legendInfo(): LegendInfo {
    const anim = useFeaAnimationStore.getState();
    const colors = useColorStore.getState();
    const field = anim.manifest?.fields.find((f) => f.name_canonical === anim.fieldName) ?? null;
    const unit = selectedResultUnit(field, anim.reduction);
    return {
        title: field?.group_path?.join(" / ") ?? field?.name_canonical ?? anim.fieldName ?? "",
        subtitle: `${anim.reduction}${unit ? ` [${unit}]` : ""}`,
        min: colors.min,
        max: colors.max,
        colormap: anim.colormap,
        showBar: anim.resultColorsVisible && !field?.value_labels,
        timeHistory: anim.timeHistory,
    };
}

function download(blob: Blob, name: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** The orientation gizmo's own canvas (an <orientation-gizmo> element), if mounted. */
function gizmoCanvas(): HTMLCanvasElement | null {
    return document.querySelector<HTMLCanvasElement>("orientation-gizmo canvas");
}

/** Copy the gizmo into the bottom-right corner, sized relative to the frame. */
function drawGizmo(ctx: CanvasRenderingContext2D, w: number, h: number, gizmo: HTMLCanvasElement) {
    if (gizmo.width === 0 || gizmo.height === 0) return;
    const size = Math.round(Math.min(w, h) * 0.16);
    const pad = Math.round(Math.min(w, h) * 0.02);
    ctx.drawImage(gizmo, w - size - pad, h - size - pad, size, size);
}

/** A copy of the view camera with the export's aspect, so the live camera is untouched. */
function exportCamera(camera: THREE.Camera, aspect: number): THREE.Camera {
    const cam = camera.clone();
    if (cam instanceof THREE.PerspectiveCamera) {
        cam.aspect = aspect;
        cam.updateProjectionMatrix();
    } else if (cam instanceof THREE.OrthographicCamera) {
        // Keep the vertical extent, widen / narrow the horizontal one around its centre.
        const cx = (cam.left + cam.right) / 2;
        const halfH = (cam.top - cam.bottom) / 2;
        cam.left = cx - halfH * aspect;
        cam.right = cx + halfH * aspect;
        cam.updateProjectionMatrix();
    }
    return cam;
}

/** Render the planned frames of the active FEA session and download the result. */
export async function exportFeaAnimation(options: ExportOptions): Promise<void> {
    const settings = normaliseExportSettings(options.settings);
    const runtime = getViewerRuntime();
    const renderer = runtime.renderer.current;
    const scene = runtime.scene.current;
    const camera = runtime.camera.current;
    const anim = useFeaAnimationStore.getState();
    if (!renderer || !scene || !camera) throw new Error("viewer is not ready");
    if (!anim.sessionActive || !anim.mesh) throw new Error("no FEA result is loaded");

    const field = anim.manifest?.fields.find((f) => f.name_canonical === anim.fieldName) ?? null;
    const plan = planExportFrames({
        timeHistory: anim.timeHistory,
        nSteps: anim.nSteps,
        stepIndex: anim.stepIndex,
        range: anim.range,
        period: anim.period,
        stepLabels: anim.timeHistory
            ? (field?.steps ?? []).map((s) => formatStepTime(s.value, field!.steps.map((x) => x.value)))
            // Slots, so a load combination's frame is labelled too (fea/caseSteps.ts).
            : mergeCaseSteps(anim.manifest, field).map((s) => s.name ?? s.label),
        fps: settings.fps,
    });
    if (plan.stepsChange && !anim.applyStep) throw new Error("the result cannot change step");

    const src = renderer.domElement;
    const viewSize = new THREE.Vector2();
    renderer.getSize(viewSize);
    const maxEdge = Math.min(renderer.capabilities.maxTextureSize || 4096, 8192);
    const [w, h] = resolveExportSize(settings, viewSize.x, viewSize.y, maxEdge);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", {willReadFrequently: settings.format === "gif"})!;
    ctx.imageSmoothingQuality = "high";
    const bg = backgroundFill(renderer, scene);
    const cam = exportCamera(camera, w / h);
    const gizmo = settings.gizmo ? gizmoCanvas() : null;

    const original = {stepIndex: anim.stepIndex, factor: anim.factor, isPlaying: anim.isPlaying};
    const originalPixelRatio = renderer.getPixelRatio();
    anim.setIsPlaying(false);
    const sink =
        settings.format === "mp4"
            ? await (await import("./mp4Sink")).createMp4Sink(canvas, plan.fps)
            : await createGifSink(canvas, plan.fps);

    // Own the renderer: no loop draws, drawing buffer at export size (CSS size kept).
    setRenderSuspended(true);
    renderer.setPixelRatio(1);
    renderer.setSize(w, h, false);
    let finished = false;
    try {
        for (let i = 0; i < plan.frames.length; i++) {
            if (options.signal?.aborted) throw new DOMException("export cancelled", "AbortError");
            const frame = plan.frames[i];
            const state = useFeaAnimationStore.getState();
            if (plan.stepsChange) {
                state.setFactor(frame.factor);
                state.setStepIndex(frame.stepIndex);
                await state.applyStep!(frame.stepIndex);
            }
            const mesh = useFeaAnimationStore.getState().mesh;
            if (mesh?.morphTargetInfluences) {
                mesh.morphTargetInfluences[0] = frame.factor * useFeaAnimationStore.getState().scaleFactor;
            }
            runtime.updateLight.current?.();
            // A browser may cap the drawing buffer below what was asked for; read
            // back what it actually holds, then render and copy in the same task
            // (the buffer is only guaranteed intact until the browser composites).
            const gl = renderer.getContext();
            const bufW = gl.drawingBufferWidth;
            const bufH = gl.drawingBufferHeight;
            renderer.render(scene, cam);
            ctx.fillStyle = bg;
            ctx.fillRect(0, 0, w, h);
            ctx.drawImage(src, 0, 0, bufW, bufH, 0, 0, w, h);
            if (gizmo) drawGizmo(ctx, w, h, gizmo);
            if (settings.legend) drawOverlay(ctx, w, h, legendInfo(), frame);
            await sink.add(i);
            options.onProgress?.({done: i + 1, total: plan.frames.length});
        }
        const blob = await sink.finish();
        finished = true;
        download(blob, exportFileName(anim.sourceName, anim.fieldName, settings.format));
    } finally {
        if (!finished) await sink.cancel().catch(() => {});
        renderer.setPixelRatio(originalPixelRatio);
        renderer.setSize(viewSize.x, viewSize.y, false);
        setRenderSuspended(false);
        const state = useFeaAnimationStore.getState();
        state.setFactor(original.factor);
        if (plan.stepsChange && state.stepIndex !== original.stepIndex) {
            state.setStepIndex(original.stepIndex);
            await state.applyStep?.(original.stepIndex);
        }
        const mesh = useFeaAnimationStore.getState().mesh;
        if (mesh?.morphTargetInfluences) {
            mesh.morphTargetInfluences[0] = original.factor * useFeaAnimationStore.getState().scaleFactor;
        }
        state.setIsPlaying(original.isPlaying);
    }
}
