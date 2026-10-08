// MP4 encoding for the animation export. Its own module, imported on demand by
// exportAnimation.ts: the NAMED imports below let the bundler tree-shake
// mediabunny down to the MP4 writer and the canvas source (a namespace
// `await import("mediabunny")` kept the whole library, demuxers and all).

import {BufferTarget, CanvasSource, Mp4OutputFormat, Output, QUALITY_HIGH, canEncodeVideo} from "mediabunny";

import type {FrameSink} from "./frameSink";

export async function createMp4Sink(canvas: HTMLCanvasElement, fps: number): Promise<FrameSink> {
    let codec: "avc" | "hevc" | "vp9" | "av1" | null = null;
    for (const candidate of ["avc", "hevc", "vp9", "av1"] as const) {
        if (await canEncodeVideo(candidate, {width: canvas.width, height: canvas.height})) {
            codec = candidate;
            break;
        }
    }
    if (codec === null) {
        throw new Error("This browser cannot encode video (no WebCodecs encoder). Export as GIF instead.");
    }
    const target = new BufferTarget();
    const output = new Output({format: new Mp4OutputFormat(), target});
    const source = new CanvasSource(canvas, {codec, quality: QUALITY_HIGH});
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
