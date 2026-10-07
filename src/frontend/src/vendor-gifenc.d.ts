// gifenc ships no type declarations. Only the subset the animation export uses.
declare module "gifenc" {
    export type GifPalette = number[][];
    export interface GifFrameOptions {
        palette?: GifPalette;
        delay?: number;
        repeat?: number;
        transparent?: boolean;
        transparentIndex?: number;
        first?: boolean;
    }
    export interface GifEncoderStream {
        writeFrame(index: Uint8Array, width: number, height: number, opts?: GifFrameOptions): void;
        finish(): void;
        bytes(): Uint8Array;
    }
    export function GIFEncoder(opts?: {auto?: boolean; initialCapacity?: number}): GifEncoderStream;
    export function quantize(
        rgba: Uint8Array | Uint8ClampedArray,
        maxColors: number,
        opts?: {format?: "rgb565" | "rgb444" | "rgba4444"; oneBitAlpha?: boolean | number},
    ): GifPalette;
    export function applyPalette(
        rgba: Uint8Array | Uint8ClampedArray,
        palette: GifPalette,
        format?: "rgb565" | "rgb444" | "rgba4444",
    ): Uint8Array;
}
