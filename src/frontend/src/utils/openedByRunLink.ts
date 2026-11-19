// Whether this page was opened by a run's link: a URL that names what to show, with
// ``?procedural=`` (a model) or ``?file=`` (a file) -- what a local viewer's launcher
// opens after a run.
//
// WHY. Such a page is about that run. Jobs this browser remembers from an earlier
// session (the toast's `resumeTrackedJobs`, the `my-jobs` restore) belong to some other
// run -- browser storage is per origin, so with local viewers it is shared by every
// store ever served on the same port, and the server that ran them may be long gone.
// Restoring them showed a stranger's "restoring" toast over the run the user asked for.
//
// CAPTURED AT MODULE LOAD, which is why index.tsx imports this first: useUrlParamLoad
// strips the link's params from the URL once it has read them, so anything asking later
// would see a plain page.

/** Whether ``search`` (a location's query string) is a run's link. Pure, for tests. */
export function isRunLinkSearch(search: string): boolean {
    const params = new URLSearchParams(search);
    return !!(params.get("procedural") || params.get("file"));
}

const captured: boolean = (() => {
    try {
        return isRunLinkSearch(globalThis.location?.search ?? "");
    } catch {
        return false;
    }
})();

/** Whether the page was opened by a run's link (read at load, before the params were stripped). */
export function openedByRunLink(): boolean {
    return captured;
}
