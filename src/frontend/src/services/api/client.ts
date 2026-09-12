// Shared plumbing for every domain module under services/api/: the fetch
// wrapper (auth header, 401 refresh-then-retry, IdP bounce), the error type
// every method throws, and a couple of small building blocks reused by more
// than one domain (folder-move grouping, query-string assembly).
//
// Pure module — no React, no zustand. Domain modules import only from here
// (plus whatever backend-shaped types they need); callers compose with
// stores via the aggregating facade in services/viewerApi.ts.

import {
  getAccessToken,
  isAuthEnabled,
  refreshAccessToken,
  signIn,
} from "@/services/auth/oidc";

// Known-good target formats keep autocomplete on the call sites that
// hardcode a value (the GLB auto-convert path on upload, etc.), while
// the ``(string & {})`` trailer keeps the type open for whatever new
// targets the worker matrix advertises (.stl, .obj, .step, …) without
// each new pair needing a frontend release.
export type TargetFormat = "glb" | "ifc" | "xml" | (string & {});

export type ConvertStatus =
  | "queued"
  | "running"
  | "done"
  | "error"
  | "cancelled";

/** Wire-format scope identifier, one of: "shared", "user:me",
 *  "project:<id>". `user:me` is resolved server-side to the caller's
 *  sub so URLs are user-agnostic. */
export type ScopeUrl = string;

/** Group keys under ``oldFolder`` by their parent path relative to it,
 * mapping each group to its ``<newFolder>/<relative_parent>`` move
 * destination. Shared by the user and admin folder rename/move flows —
 * the move endpoint flattens inputs into one target folder, so a single
 * batch call would lose the folder's internal structure. */
export function groupKeysByRelativeParent(
  oldFolder: string,
  newFolder: string,
  allKeys: string[],
): Map<string, string[]> {
  const oldTrimmed = oldFolder.replace(/^\/+|\/+$/g, "");
  const newTrimmed = newFolder.replace(/^\/+|\/+$/g, "");
  const prefix = oldTrimmed + "/";
  const groups = new Map<string, string[]>();
  for (const k of allKeys) {
    if (!k.startsWith(prefix)) continue;
    const rest = k.slice(prefix.length);
    const lastSlash = rest.lastIndexOf("/");
    const relParent = lastSlash >= 0 ? rest.slice(0, lastSlash) : "";
    const dest = relParent ? `${newTrimmed}/${relParent}` : newTrimmed;
    if (!groups.has(dest)) groups.set(dest, []);
    groups.get(dest)!.push(k);
  }
  return groups;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function readDetail(r: Response): Promise<string> {
  try {
    return await r.text();
  } catch {
    return "";
  }
}

export async function jsonOrThrow<T>(r: Response, what: string): Promise<T> {
  if (!r.ok) {
    throw new ApiError(
      `${what} failed: ${r.status} ${r.statusText}`,
      r.status,
      await readDetail(r),
    );
  }
  return (await r.json()) as T;
}

export function authHeader(): Record<string, string> {
  const t = getAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
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
export async function authedFetch(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  // Pre-flight: if our cached token has fallen out of the 30s skew
  // window, refresh before sending rather than letting the request
  // 401 first. Background pollers (e.g. the admin audit badge) fire
  // on a timer and would otherwise log a browser-level 401 on every
  // tick that straddles a token expiry.
  if (isAuthEnabled() && !getAccessToken()) {
    await refreshAccessToken();
  }
  const merged: RequestInit = {
    ...init,
    headers: {
      ...(init.headers as Record<string, string> | undefined),
      ...authHeader(),
    },
  };
  let r = await fetch(url, merged);
  if (r.status === 401 && isAuthEnabled()) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      r = await fetch(url, {
        ...init,
        headers: {
          ...(init.headers as Record<string, string> | undefined),
          ...authHeader(),
        },
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

/** ``?a=1&b=2`` for a non-empty ``URLSearchParams``, else ``""``. Several
 * admin listing endpoints build an optional filter/paging query the same
 * way; this is the one place that decides whether the ``?`` is worth it. */
export function toQueryString(params: URLSearchParams): string {
  const s = params.toString();
  return s ? `?${s}` : "";
}
