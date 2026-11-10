// Gunzip bytes into a Blob with the browser's own decompressor: it runs off the main thread and at
// native speed, where inflating in JavaScript holds the page for hundreds of milliseconds on a large
// model, and it never holds the inflated bytes as a second array beside the Blob.
import {ungzip} from "pako";

/** The gunzipped `data`, as a Blob of `type`. */
export async function gunzipToBlob(data: Uint8Array, type: string): Promise<Blob> {
    if (typeof DecompressionStream === "undefined") return new Blob([ungzip(data) as BlobPart], {type});
    const inflated = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(inflated, {headers: {"Content-Type": type}}).blob();
}
