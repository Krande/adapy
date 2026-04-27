// Typed client for the hosted viewer's REST API. Every fetch against
// /api/* should go through this module so the URL shape, error
// handling, auth header, and types live in one place.
//
// Pure module — no React, no zustand. Callers compose with stores.

import {runtime} from "@/runtime/config";
import {getAccessToken, isAuthEnabled, refreshAccessToken, signIn} from "@/services/auth/oidc";

export type TargetFormat = "glb" | "ifc" | "xml";
export type ConvertStatus = "queued" | "running" | "done" | "error";

/** Wire-format scope identifier, one of: "shared", "user:me",
 *  "project:<id>". `user:me` is resolved server-side to the caller's
 *  sub so URLs are user-agnostic. */
export type ScopeUrl = string;

export interface MeResponse {
    sub: string;
    email: string;
    displayName: string;
    isAdmin: boolean;
    scopes: Array<{kind: "shared" | "user" | "project"; id: string | null; name: string}>;
    projects: Array<{id: string; slug: string; name: string; role: string}>;
}

export interface FileEntry {
    key: string;
    size: number;
}

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
    scope_kind?: string;
    scope_id?: string | null;
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

function authHeader(): Record<string, string> {
    const t = getAccessToken();
    return t ? {Authorization: `Bearer ${t}`} : {};
}

/**
 * Fetch with auth handling. Attaches the bearer token, and on a 401
 * tries one refresh-then-retry. If still unauthorized, redirects to
 * the IdP — by the time the user comes back, the SPA boots fresh and
 * resumes whatever it was doing.
 *
 * Routes that aren't gated server-side (e.g. /api/config) work
 * regardless because they don't return 401.
 */
async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
    const merged: RequestInit = {
        ...init,
        headers: {...(init.headers as Record<string, string> | undefined), ...authHeader()},
    };
    let r = await fetch(url, merged);
    if (r.status === 401 && isAuthEnabled()) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
            r = await fetch(url, {
                ...init,
                headers: {...(init.headers as Record<string, string> | undefined), ...authHeader()},
            });
            if (r.status !== 401) return r;
        }
        // No path forward — bounce through the IdP. The current URL
        // is preserved as the post-sign-in return target.
        await signIn(window.location.pathname + window.location.search);
        // signIn navigates away, but if it doesn't (popup blocker?),
        // surface the original 401 so callers don't hang.
    }
    return r;
}

export const viewerApi = {
    /** Direct URL for the addressable blob endpoint. Includes scope.
     * Only safe to use as `<a href download>` when auth is disabled —
     * with auth on use :func:`downloadBlob` so the bearer token rides
     * along. */
    blobUrl(scope: ScopeUrl, key: string): string {
        return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
    },

    /** Bootstrap the SPA's identity + available scopes. */
    async me(): Promise<MeResponse> {
        const r = await authedFetch(`${runtime.apiBase()}/me`);
        return jsonOrThrow<MeResponse>(r, "me");
    },

    async listFiles(scope: ScopeUrl): Promise<FileEntry[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/files`,
        );
        const body = await jsonOrThrow<{files: FileEntry[]}>(r, `listFiles(${scope})`);
        return body.files;
    },

    /** Trigger a browser download of a stored blob. Fetches with auth,
     * materialises a blob: URL, clicks a hidden anchor, then revokes
     * the URL to release memory. Works in both auth-on and auth-off
     * modes — the only cost over `<a href>` is one extra round-trip
     * the browser would have made anyway. */
    async downloadBlob(scope: ScopeUrl, key: string, suggestedName: string): Promise<void> {
        const r = await authedFetch(this.blobUrl(scope, key));
        if (!r.ok) {
            throw new ApiError(`downloadBlob(${key})`, r.status, await readDetail(r));
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        try {
            const a = document.createElement("a");
            a.href = url;
            a.download = suggestedName;
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);
        }
    },

    /** Fetch raw bytes for a key. Used by the in-browser Pyodide
     * pipeline to read its source from storage. */
    async getBlob(scope: ScopeUrl, key: string): Promise<ArrayBuffer> {
        const r = await authedFetch(this.blobUrl(scope, key));
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
        scope: ScopeUrl,
        key: string,
        body: BodyInit,
        opts?: {onProgress?: (loaded: number, total: number) => void},
    ): Promise<void> {
        if (!opts?.onProgress) {
            const r = await authedFetch(this.blobUrl(scope, key), {
                method: "PUT",
                body,
                headers: {"Content-Type": "application/octet-stream"},
            });
            if (!r.ok) {
                throw new ApiError(`putBlob(${key})`, r.status, await readDetail(r));
            }
            return;
        }

        // Progress-tracked path uses XHR. We don't get authedFetch's
        // refresh-then-retry, but the access token's 30s skew window
        // makes mid-upload expiry vanishingly unlikely.
        await new Promise<void>((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("PUT", this.blobUrl(scope, key));
            xhr.setRequestHeader("Content-Type", "application/octet-stream");
            const t = getAccessToken();
            if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
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
    async convert(
        scope: ScopeUrl,
        sourceKey: string,
        targetFormat: TargetFormat = "glb",
    ): Promise<ConvertResponse> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/convert`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({source_key: sourceKey, target_format: targetFormat}),
            },
        );
        return jsonOrThrow<ConvertResponse>(r, `convert(${sourceKey} -> ${targetFormat})`);
    },

    /** Poll a single conversion job by id. Job_id is globally unique,
     * so the URL doesn't carry a scope — the server re-checks access
     * against the scope recorded on the job. */
    async convertStatus(jobId: string): Promise<ConvertResponse> {
        const r = await authedFetch(`${runtime.apiBase()}/convert/${encodeURIComponent(jobId)}`);
        return jsonOrThrow<ConvertResponse>(r, `convertStatus(${jobId})`);
    },

    /** Server-side viable-target listing. The frontend mirrors this
     * mapping client-side too, but this lets us cross-check. */
    async convertTargets(scope: ScopeUrl, sourceKey: string): Promise<TargetFormat[]> {
        const url =
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}` +
            `/convert/targets?source_key=${encodeURIComponent(sourceKey)}`;
        const r = await authedFetch(url);
        if (!r.ok) return [];
        const body = (await r.json()) as ConvertTargetsResponse;
        return body.targets || [];
    },
};

export {ApiError};
