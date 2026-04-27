// Typed client for the hosted viewer's REST API. Every fetch against
// /api/* should go through this module so the URL shape, error
// handling, and types live in one place.
//
// Pure module — no React, no zustand. Callers compose with stores.

import {runtime} from "@/runtime/config";

export type TargetFormat = "glb" | "ifc" | "xml";
export type ConvertStatus = "queued" | "running" | "done" | "error";

export interface ConvertResponse {
    job_id: string;
    source_key: string;
    derived_key: string;
    target_format?: TargetFormat;
    status: ConvertStatus;
    progress: number;
    stage: string;
    error: string | null;
    cached: boolean;
}

export interface ConvertTargetsResponse {
    source_key: string;
    targets: TargetFormat[];
}

class ApiError extends Error {
    constructor(message: string, public status: number, public detail?: string) {
        super(message);
        this.name = "ApiError";
    }
}

async function readDetail(r: Response): Promise<string> {
    try {
        return await r.text();
    } catch {
        return "";
    }
}

async function jsonOrThrow<T>(r: Response, what: string): Promise<T> {
    if (!r.ok) {
        throw new ApiError(`${what} failed: ${r.status} ${r.statusText}`, r.status, await readDetail(r));
    }
    return (await r.json()) as T;
}

export const viewerApi = {
    /** Direct, addressable URL for an `<a href download>` or `<img src>`. */
    blobUrl(key: string): string {
        return `${runtime.apiBase()}/blobs/${encodeURIComponent(key)}`;
    },

    /** Fetch raw bytes for a key. Used by the in-browser Pyodide
     * pipeline to read its source from storage. */
    async getBlob(key: string): Promise<ArrayBuffer> {
        const r = await fetch(this.blobUrl(key));
        if (!r.ok) {
            throw new ApiError(`getBlob(${key})`, r.status, await readDetail(r));
        }
        return await r.arrayBuffer();
    },

    /** Upload bytes under a given key. body is anything fetch/XHR can
     * send (File, Blob, ArrayBuffer, ...). When `onProgress` is given,
     * the request goes through XMLHttpRequest because fetch doesn't
     * expose upload progress consistently across browsers. */
    async putBlob(
        key: string,
        body: BodyInit,
        opts?: {onProgress?: (loaded: number, total: number) => void},
    ): Promise<void> {
        if (!opts?.onProgress) {
            const r = await fetch(this.blobUrl(key), {
                method: "PUT",
                body,
                headers: {"Content-Type": "application/octet-stream"},
            });
            if (!r.ok) {
                throw new ApiError(`putBlob(${key})`, r.status, await readDetail(r));
            }
            return;
        }

        await new Promise<void>((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("PUT", this.blobUrl(key));
            xhr.setRequestHeader("Content-Type", "application/octet-stream");
            xhr.upload.addEventListener("progress", (e) => {
                if (e.lengthComputable) {
                    opts.onProgress!(e.loaded, e.total);
                }
            });
            xhr.addEventListener("load", () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve();
                } else {
                    reject(
                        new ApiError(
                            `putBlob(${key}) failed: ${xhr.status}`,
                            xhr.status,
                            xhr.responseText || "",
                        ),
                    );
                }
            });
            xhr.addEventListener("error", () =>
                reject(new ApiError(`putBlob(${key}) network error`, 0, "")),
            );
            xhr.addEventListener("abort", () =>
                reject(new ApiError(`putBlob(${key}) aborted`, 0, "")),
            );
            xhr.send(body as XMLHttpRequestBodyInit);
        });
    },

    /** Enqueue a server-side conversion. Returns either a fresh queued
     * job, a synthesised "cached" response (derived already present),
     * or rejects with ApiError. */
    async convert(sourceKey: string, targetFormat: TargetFormat = "glb"): Promise<ConvertResponse> {
        const r = await fetch(`${runtime.apiBase()}/convert`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({source_key: sourceKey, target_format: targetFormat}),
        });
        return jsonOrThrow<ConvertResponse>(r, `convert(${sourceKey} -> ${targetFormat})`);
    },

    /** Poll a single conversion job by id. */
    async convertStatus(jobId: string): Promise<ConvertResponse> {
        const r = await fetch(`${runtime.apiBase()}/convert/${encodeURIComponent(jobId)}`);
        return jsonOrThrow<ConvertResponse>(r, `convertStatus(${jobId})`);
    },

    /** Server-side viable-target listing. The frontend mirrors this
     * mapping client-side too, but this lets us cross-check. */
    async convertTargets(sourceKey: string): Promise<TargetFormat[]> {
        const url = `${runtime.apiBase()}/convert/targets?source_key=${encodeURIComponent(sourceKey)}`;
        const r = await fetch(url);
        if (!r.ok) return [];
        const body = (await r.json()) as ConvertTargetsResponse;
        return body.targets || [];
    },
};

export {ApiError};
