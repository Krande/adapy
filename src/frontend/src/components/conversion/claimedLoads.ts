// Loads whose progress another surface tells (a host's own file rows, say), taken out of
// the progress toast so one load is not reported twice. Kept free of React and the stores
// so the rule can be tested.

/** Is this load told elsewhere? `name` is the source name; for a conversion-store entry,
 *  `jobKey` is its store key (`${source}::${target}`) and `name` the part before `::`. */
export type IsClaimed = (name: string, jobKey?: string) => boolean;

/** Claimed by either predicate: a host's own claims added to core's (a load claimed
 *  with `claimLoad` stays out of the toast whatever the host passes). */
export function eitherClaimed(a: IsClaimed, b?: IsClaimed): IsClaimed {
    return b ? (name, jobKey) => a(name, jobKey) || b(name, jobKey) : a;
}

/** The source part of a conversion-store key. */
export function jobSource(key: string): string {
    const i = key.indexOf("::");
    return i < 0 ? key : key.slice(0, i);
}

/** Whether a conversion-store entry is claimed; never without a predicate. */
export function isClaimedJob(key: string, isClaimed?: IsClaimed): boolean {
    return !!isClaimed && isClaimed(jobSource(key), key);
}

/** The load-queue part of the toast with claimed loads taken out. The current load goes as
 *  a whole, its model-download step included. */
export function withoutClaimed<Q extends {name: string}, E extends {name: string}>(
    load: {currentName: string | null; queued: Q[]; errors: E[]},
    isClaimed?: IsClaimed,
): {currentClaimed: boolean; queued: Q[]; errors: E[]} {
    if (!isClaimed) return {currentClaimed: false, queued: load.queued, errors: load.errors};
    return {
        currentClaimed: load.currentName !== null && isClaimed(load.currentName),
        queued: load.queued.filter((t) => !isClaimed(t.name)),
        errors: load.errors.filter((e) => !isClaimed(e.name)),
    };
}
