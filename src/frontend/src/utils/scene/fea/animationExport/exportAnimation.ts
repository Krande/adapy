// Export the active FEA animation as an MP4 or a GIF.
//
// Deterministic, not a screen recording: every frame of the plan is applied (a
// step fetch for a time history, a deformation factor otherwise), rendered once
// by the viewer's own renderer and copied straight off its canvas -- so the
// export has exactly the colours, lighting and antialiasing on screen, and
// neither network stalls nor a slow device drop frames. A legend (field, unit,
// colour bar, range) and the time / case label are burned into each frame,
// since a contour animation without its scale is unreadable once it leaves the
// viewer.
//
// The encoders are imported on demand, so they cost nothing until an export.

import * as THREE from "three";

import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {getColormap} from "@/utils/scene/fea/colormaps";
import {selectedResultUnit} from "@/utils/scene/fea/resultUnits";

import {evenSize, exportFileName, planExportFrames, type ExportFrame} from "./framePlan";

export type AnimationFormat = "mp4" | "gif";

export interface ExportProgress {
    done: number;
    total: number;
}

export interface ExportOptions {
    format: AnimationFormat;
    onProgress?: (p: ExportProgress) => void;
    signal?: AbortSignal;
}

/** Longest output edge per format. GIF stays smaller: it is uncompressed-ish and
 * meant for chats and slides. */
const MAX_EDGE: Record<AnimationFormat, [number, number]> = {mp4: [1280, 720], gif: [800, 800]};

interface FrameSink {
    add(index: number): Promise<void>;
    finish(): Promise<Blob>;
    cancel(): Promise<void>;
}

async function createMp4Sink(canvas: HTMLCanvasElement, fps: number): Promise<FrameSink> {
    const mb = await import("mediabunny");
    let codec: "avc" | "hevc" | "vp9" | "av1" | null = null;
    for (const candidate of ["avc", "hevc", "vp9", "av1"] as const) {
        if (await mb.canEncodeVideo(candidate, {width: canvas.width, height: canvas.height})) {
            codec = candidate;
            break;
        }
    }
    if (codec === null) {
        throw new Error("This browser cannot encode video (no WebCodecs encoder). Export as GIF instead.");
    }
    const target = new mb.BufferTarget();
    const output = new mb.Output({format: new mb.Mp4OutputFormat(), target});
    const source = new mb.CanvasSource(canvas, {codec, quality: mb.QUALITY_HIGH});
    output.addVideoTrack(source, {frameRate: fps});
    await output.start();
    return {
        add: (index) => source.add(index / fps, 1 / fps),
        finish: async () => {
            await output.finalize();
            return new Blob([target.buffer!], {type: "video/mp4"});
        },
        cancel: () => output.cancel(),
    };
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

/** Render the planned frames of the active FEA session and download the result. */
export async function exportFeaAnimation(options: ExportOptions): Promise<void> {
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
        stepLabels: field?.steps.map((s) => s.label) ?? [],
    });
    if (plan.stepsChange && !anim.applyStep) throw new Error("the result cannot change step");

    const src = renderer.domElement;
    const [maxW, maxH] = MAX_EDGE[options.format];
    const fit = Math.min(maxW / src.width, maxH / src.height, 1);
    const [w, h] = evenSize(src.width * fit, src.height * fit);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", {willReadFrequently: options.format === "gif"})!;
    ctx.imageSmoothingQuality = "high";
    const bg = backgroundFill(renderer, scene);

    const original = {stepIndex: anim.stepIndex, factor: anim.factor, isPlaying: anim.isPlaying};
    anim.setIsPlaying(false);
    const sink = options.format === "mp4" ? await createMp4Sink(canvas, plan.fps) : await createGifSink(canvas, plan.fps);
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
            // Render and copy in the same task: the drawing buffer is only
            // guaranteed intact until the browser composites it.
            renderer.render(scene, camera);
            ctx.fillStyle = bg;
            ctx.fillRect(0, 0, w, h);
            ctx.drawImage(src, 0, 0, w, h);
            drawOverlay(ctx, w, h, legendInfo(), frame);
            await sink.add(i);
            options.onProgress?.({done: i + 1, total: plan.frames.length});
        }
        const blob = await sink.finish();
        finished = true;
        download(blob, exportFileName(anim.sourceName, anim.fieldName, options.format));
    } finally {
        if (!finished) await sink.cancel().catch(() => {});
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
