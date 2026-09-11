// Derives a SAVE_PROCEDURAL_MODEL-safe model_id from a viewer source name.
//
// The websocket path has no per-scope store to open a model BY id the way the hosted viewer
// does -- there's just whatever name the process gave the scene it pushed. The save handler
// (`ada.comms.msg_handling.save_procedural_model`) validates `model_id` as an opaque token --
// letters/digits/'.'/'_'/'-' only, 1-128 characters, starting with a letter or digit -- and
// rejects anything else before it ever touches disk (docs/documents/ws_rest_parity.rst, "model
// id: opaque token vs file path"). A raw source name can carry spaces, colons, slashes; none of
// that is valid there, so it is sanitised into something that is rather than passed through and
// left to fail on the first commit.

const FALLBACK_MODEL_ID = "model";
const MAX_MODEL_ID_LENGTH = 128;

export function localModelIdFromSourceName(sourceName: string | null | undefined): string {
  const cleaned = (sourceName ?? "")
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    // The handler requires the FIRST character to be a letter or digit.
    .replace(/^[^A-Za-z0-9]+/, "")
    .slice(0, MAX_MODEL_ID_LENGTH);
  return cleaned || FALLBACK_MODEL_ID;
}
