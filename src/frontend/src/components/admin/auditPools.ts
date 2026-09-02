import type {WorkerEntry} from "@/services/viewerApi";

// Turning the online worker registry into the audit-run pool picker.
//
// The picker used to list every distinct capability tag, which was right when
// each capability was its own image: choosing "abaqus" chose a fleet. With one
// combined image advertising all of them, six choices resolve to the same two
// pods, and the control stopped meaning anything.
//
// So it groups by IMAGE, which is the axis an operator actually reasons about
// — "run this corpus on the build I just deployed".
//
// WHAT IT CANNOT DO, stated plainly because the UI must not imply otherwise:
// a run is routed by NATS SUBJECT, derived from a capability. Two images
// advertising the same capability share a consumer, so selecting one of them
// cannot bind — whichever worker pulls first runs the cell. That is what
// ``enforceable`` reports, and the picker says so rather than pretending.

export interface ImagePool {
    /** The image tag, or "" for workers that never reported one. */
    imageTag: string;
    /** Online workers running it. */
    replicas: number;
    /** Every capability these workers advertise, sorted. */
    capabilities: string[];
    /** The capability a run targeting this image is dispatched to. */
    routeCapability: string;
    /** False when another image also advertises ``routeCapability`` — the
     * restriction is then advisory, because the subject reaches both. */
    enforceable: boolean;
}

/** Capability an audit sweep should be dispatched to, given what an image
 * serves. A sweep converts source files, which is the ``base`` pool's job;
 * a specialised image that does not serve ``base`` falls back to its first
 * capability so it is still reachable. */
function routeFor(capabilities: string[]): string {
    if (capabilities.includes("base")) return "base";
    return capabilities[0] ?? "";
}

export function groupWorkersByImage(workers: readonly WorkerEntry[]): ImagePool[] {
    const byImage = new Map<string, {replicas: number; caps: Set<string>}>();

    for (const w of workers) {
        if (!w.online) continue;
        const tag = (w.image_tag || "").trim();
        const bucket = byImage.get(tag) ?? {replicas: 0, caps: new Set<string>()};
        bucket.replicas += 1;
        for (const c of w.capabilities || []) {
            const v = c.trim().toLowerCase();
            if (v) bucket.caps.add(v);
        }
        byImage.set(tag, bucket);
    }

    const pools: ImagePool[] = [...byImage.entries()].map(([imageTag, b]) => {
        const capabilities = [...b.caps].sort();
        return {
            imageTag,
            replicas: b.replicas,
            capabilities,
            routeCapability: routeFor(capabilities),
            enforceable: true,
        };
    });

    // A route is only enforceable while exactly one image claims it.
    const claims = new Map<string, number>();
    for (const p of pools) {
        if (!p.routeCapability) continue;
        claims.set(p.routeCapability, (claims.get(p.routeCapability) ?? 0) + 1);
    }
    for (const p of pools) {
        p.enforceable = !!p.routeCapability && (claims.get(p.routeCapability) ?? 0) === 1;
    }

    // Newest-looking first is not knowable from a tag, so order by fleet size
    // then tag: the pool doing the work is the one you most likely mean.
    pools.sort((a, b) => b.replicas - a.replicas || a.imageTag.localeCompare(b.imageTag));
    return pools;
}

/** Label for one entry: the tag, its fleet size, and what it serves. */
export function describeImagePool(p: ImagePool): string {
    const tag = p.imageTag || "(no image tag)";
    const replicas = `${p.replicas} replica${p.replicas === 1 ? "" : "s"}`;
    return `${tag} — ${replicas}`;
}
