// Which clash-check ENGINE a run uses, and where it runs -- pure functions of the store's state.
//
// CONVEX CONTACT IS THE DEFAULT. The built-in checker (`adapy`) is a member-CONNECTIVITY check: it
// reads the member model and asks which members are meant to connect -- beam axes that meet at a
// shared node, beam mid-spans inside a plate's padded bounding box, plates meeting edge-on. It never
// measures the solids, so two members whose steel overlaps but whose axes do not meet are not found,
// and nothing it reports carries a contact normal, a depth or a patch. The convex-contact check
// (adacpp's `adacpp_clash` wasm module, the same C++ a worker runs) measures the solids the viewer
// shows and is what a clash check is expected to be.
//
// So a run uses convex contact unless the user picked another engine, or convex contact cannot run
// here at all -- this viewer's wasm module does not load AND no worker lists it. Then the run falls
// back to the built-in check and SAYS so (`checkerNote`), rather than failing or silently changing
// what "clash" means.

import type { WireClashChecker } from "@/services/api/clashCheck";
import { CONTACT_ORIGIN } from "@/services/clash/contactRuns";
import { BUILTIN_CHECKER } from "./result";

/** What the panel calls core's connectivity check where the deployment does not name it. */
export const BUILTIN_CHECKER_LABEL = "Member connectivity (built-in)";
/** What core's check does, said where it is offered: it is not an interference check. */
export const BUILTIN_CHECKER_DESCRIPTION =
  "Which members are meant to connect, read from the member model: beam axes meeting at a shared node, beams " +
  "whose mid-span lies in a plate's padded bounding box, and plates meeting edge-on. Does not measure the solids, " +
  "so it is not an interference check.";
/** What the panel calls the convex-contact check where the deployment does not name it. */
export const CONTACT_CHECKER_LABEL = "Convex contact";

/** The convex-contact checker as this viewer can run it itself, for a deployment that does not list
 *  it (an api without adacpp, or one whose listing failed). Same name, settings and defaults as
 *  `ada.clash.convex_contact.register`. */
export const BROWSER_CONTACT_CHECKER: WireClashChecker = {
  slug: CONTACT_ORIGIN,
  name: CONTACT_ORIGIN,
  label: CONTACT_CHECKER_LABEL,
  description:
    "Interference between the members' solids as the viewer shows them: finds members that touch or overlap, " +
    "with the contact normal, penetration depth, contact-patch area and patch outline. Runs in this browser " +
    "(adacpp's wasm clash module); each run is kept beside its model.",
  capability: null,
  passes: [CONTACT_ORIGIN],
  options: [
    {
      key: "security_margin",
      label: "Security margin",
      type: "number",
      default: 0.01,
      min: 0,
      unit: "m",
      help: "Solids closer than this count as touching, so members a hair apart are still found.",
    },
    {
      key: "min_contact_area",
      label: "Min contact area",
      type: "number",
      default: 1e-6,
      min: 0,
      unit: "m²",
      help: "Contacts with a smaller patch than this (a corner grazing a face) are dropped.",
    },
  ],
  uses_core_options: false,
  priority: 10,
  origin: "browser",
};

const BUILTIN_ENTRY: WireClashChecker = {
  slug: BUILTIN_CHECKER,
  name: BUILTIN_CHECKER,
  label: BUILTIN_CHECKER_LABEL,
  description: BUILTIN_CHECKER_DESCRIPTION,
  capability: null,
  uses_core_options: true,
  priority: 0,
};

/** The engines to offer: the deployment's, plus convex contact when this viewer's wasm module runs it
 *  and the deployment did not list it. `null` (nothing known) stays `null` unless the wasm module
 *  answered -- then core's and convex contact are both offerable. */
export function withBrowserContact(
  listed: readonly WireClashChecker[] | null,
  contactWasm: boolean | null,
): readonly WireClashChecker[] | null {
  if (contactWasm !== true) return listed;
  if (listed === null) return [BUILTIN_ENTRY, BROWSER_CONTACT_CHECKER];
  if (listed.some((c) => c.name === CONTACT_ORIGIN)) return listed;
  return [...listed, BROWSER_CONTACT_CHECKER];
}

/** Whether convex contact can run here: the wasm module loads, or the deployment lists it (a worker). */
export function contactOffered(available: readonly WireClashChecker[] | null, contactWasm: boolean | null): boolean {
  return contactWasm === true || Boolean(available?.some((c) => c.name === CONTACT_ORIGIN));
}

/** The sentence shown when a run that would have used convex contact uses the built-in check. */
export const CONTACT_UNAVAILABLE_NOTE =
  "Convex contact is not available here -- this viewer's clash module (adacpp_clash wasm) did not load and no " +
  "worker offers it -- so the check uses the built-in member-connectivity check instead: beam axes and plate " +
  "bounds from the member model, not the solids.";

/** The engine the next run uses, and the note to show for it.
 *
 *  - Picked explicitly: kept, unless the deployment no longer offers it (then core's, as before).
 *  - Not picked: convex contact while it can run here, or while that is not yet known (the probe has
 *    not answered); else core's, with `CONTACT_UNAVAILABLE_NOTE`. */
export function resolveChecker(state: {
  checker: string;
  checkerChosen: boolean;
  availableCheckers: readonly WireClashChecker[] | null;
  contactWasm: boolean | null;
}): { checker: string; note: string | null } {
  const { checker, checkerChosen, availableCheckers, contactWasm } = state;
  if (checkerChosen) {
    if (checker === CONTACT_ORIGIN) {
      return contactOffered(availableCheckers, contactWasm) || contactWasm === null
        ? { checker, note: null }
        : { checker: BUILTIN_CHECKER, note: CONTACT_UNAVAILABLE_NOTE };
    }
    const gone = availableCheckers && !availableCheckers.some((c) => c.name === checker);
    return { checker: gone ? BUILTIN_CHECKER : checker, note: null };
  }
  if (contactWasm === null || contactOffered(availableCheckers, contactWasm)) return { checker: CONTACT_ORIGIN, note: null };
  return { checker: BUILTIN_CHECKER, note: CONTACT_UNAVAILABLE_NOTE };
}

/** Whether the next run of `checker` measures in this browser. `inBrowser` null is the engine's own
 *  default: convex contact in the browser (its wasm module) unless that module did not load and a
 *  worker has to run it; core's on the server, which is the route every source and pass works on. */
export function runsInBrowser(state: { checker: string; inBrowser: boolean | null; contactWasm: boolean | null }): boolean {
  if (state.inBrowser !== null) return state.inBrowser;
  return state.checker === CONTACT_ORIGIN ? state.contactWasm !== false : false;
}
