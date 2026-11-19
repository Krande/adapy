// The models in the scene that were opened from the user's DISK (File -> Open, a drop on the view):
// loaded under their file name, with nothing stored behind them.
//
// A stored file is loaded under its storage key, and the two can look alike ("deck.glb" is a valid
// key), so the scene's source name alone cannot tell a saved group whether a member's model has a
// source in the store. The local-open path marks the names it loads; a load from storage clears
// the mark for its key.

const local = new Set<string>();

/** `name` was just loaded from the user's disk. */
export function markLocalSource(name: string): void {
  local.add(name);
}

/** `name` was loaded from storage: whatever was opened from disk under it before is gone. */
export function forgetLocalSource(name: string): void {
  local.delete(name);
}

/** Whether the model loaded under `name` came from the user's disk. */
export function isLocalSource(name: string): boolean {
  return local.has(name);
}
