// Is the object store reachable directly from this browser?
//
// A stored model loads from a presigned object-store URL first and falls back
// to the authed server relay when that load fails. But an unreachable store
// host (it resolves to an address this network can't route to, or a firewall
// silently drops the connection) doesn't make the load fail: the fetch hangs
// until the browser's connect timeout, which can take minutes, and then every
// following load in the session hangs the same way before falling back.
//
// So the first presigned load of a session is preceded by a short probe: a GET
// of the presigned URL, cancelled as soon as the headers arrive. Any HTTP
// response means the store answers (a 4xx still goes down the normal
// load-then-fallback path); a network error or timeout marks it unreachable
// and loads go straight to the relay. The verdict is cached for the session,
// and an "unreachable" verdict is re-checked after a while so a network change
// is picked up without a reload.

export const PROBE_TIMEOUT_MS = 5_000;
export const UNREACHABLE_RECHECK_MS = 5 * 60_000;

export interface ProbeDeps {
    fetchImpl?: typeof fetch;
    now?: () => number;
    timeoutMs?: number;
}

interface Verdict {
    reachable: boolean;
    at: number;
}

let verdict: Verdict | null = null;
let inflight: Promise<boolean> | null = null;

/** Whether presigned URLs can be fetched directly. Probes with `url` at most
 *  once per session (concurrent callers share the probe); an unreachable
 *  verdict expires after UNREACHABLE_RECHECK_MS. */
export async function directStoreReachable(url: string, deps: ProbeDeps = {}): Promise<boolean> {
    const now = deps.now ?? Date.now;
    if (verdict && (verdict.reachable || now() - verdict.at < UNREACHABLE_RECHECK_MS)) {
        return verdict.reachable;
    }
    if (!inflight) {
        inflight = probe(url, deps).then((reachable) => {
            verdict = {reachable, at: now()};
            inflight = null;
            return reachable;
        });
    }
    return inflight;
}

async function probe(url: string, deps: ProbeDeps): Promise<boolean> {
    const fetchImpl = deps.fetchImpl ?? fetch;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), deps.timeoutMs ?? PROBE_TIMEOUT_MS);
    try {
        const res = await fetchImpl(url, {signal: controller.signal, cache: "no-store"});
        // Only the headers were needed; don't download the model twice.
        res.body?.cancel().catch(() => {});
        return true;
    } catch {
        return false;
    } finally {
        clearTimeout(timer);
    }
}

/** Forget the cached verdict (tests). */
export function resetDirectStoreProbe(): void {
    verdict = null;
    inflight = null;
}
