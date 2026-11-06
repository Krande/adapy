// One-line summary of a job's current step. The worker publishes a
// ``stage`` string alongside each KV update; we surface it directly so
// users see what's actually happening ("loading", "tessellating face
// 1247/4532", "writing output") instead of just a wall-clock spinner.
// Terse codes the worker uses today get a friendlier rendering; any
// free-text the worker publishes is shown verbatim so adding a new
// stage in the worker doesn't require a frontend round trip.
// Friendly translations for the stage codes adapy's converters emit
// (see ada/comms/rest/converter.py — the ``on_progress(stage, frac)``
// callback feeds these in). The map is exhaustive against what's
// emitted today; any unrecognised stage falls through to the raw
// string (so a worker that adds a new stage code can surface it
// without a frontend deploy).
export const STAGE_LABEL: Record<string, string> = {
    queued: "Waiting for worker…",
    loading: "Loading source file…",
    parsing: "Parsing source…",
    unpacking: "Unpacking bundle…",
    translating: "Translating geometry…",
    tessellating: "Tessellating geometry…",
    "selecting-field": "Selecting FEA field…",
    writing: "Writing output…",
    "writing-ifc": "Writing IFC…",
    "writing-step": "Writing STEP…",
    "writing-xml": "Writing Genie XML…",
    "writing-gnx": "Writing Genie workspace…",
    exporting: "Exporting…",
    convert: "Converting…",
    upload: "Uploading result…",
    uploading: "Uploading result…",
    ready: "Ready",
    cached: "Cached result",
    aborted: "Aborted",
    misrouted: "Routed to wrong pool",
};

export function stageText(stage: string | undefined | null): string {
    if (!stage) return "";
    const known = STAGE_LABEL[stage];
    return known || stage;
}
