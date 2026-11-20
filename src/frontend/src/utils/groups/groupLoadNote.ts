// What a saved group's "Load into scene" did, in words: the note the Scene panel shows under the
// group, and the same words a plugin's `ctx.scene.loadSavedGroup` hands back with its counts.

import type { GroupLoadOutcome } from "./groupLoadRun";
import { describeMember, type GroupMember } from "./savedGroups";

/** The first `max` members described, and how many more. */
export function listMembers(members: readonly GroupMember[], max = 4): string {
  const shown = members.slice(0, max).map(describeMember);
  return members.length > max ? `${shown.join("; ")}; and ${members.length - max} more` : shown.join("; ");
}

/** A load's outcome as one line, warning when a member could not be loaded or was not found. */
export function groupLoadNote(out: GroupLoadOutcome): { tone: "info" | "warn"; text: string } {
  const parts: string[] = [];
  parts.push(`${out.loaded} model${out.loaded === 1 ? "" : "s"} loaded${out.already ? `, ${out.already} already in the scene` : ""}`);
  if (out.hidden) parts.push(`${out.hidden} other object${out.hidden === 1 ? "" : "s"} of the loaded documents hidden`);
  parts.push(`${out.selection.ranges} object${out.selection.ranges === 1 ? "" : "s"} selected`);
  if (out.failed.length) {
    parts.push(
      `not loaded: ${out.failed.map((f) => `${listMembers(f.members, 2)} (${f.reason})`).join("; ")}`,
    );
  }
  return { tone: out.failed.length || out.selection.notFound.length ? "warn" : "info", text: parts.join(" · ") };
}
