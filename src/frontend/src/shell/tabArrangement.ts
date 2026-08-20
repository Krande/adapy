// Whether a strip of tabs inside a panel should become a stacked column of disclosures.
//
// Distinct from dockArrangement.ts, which decides the same question for a dock's PANELS.
// The rules genuinely differ, and using one for both would get the thresholds wrong in
// each direction:
//
//   * A stacked dock panel needs its full body height, because it is always open.
//   * A stacked tab becomes a COLLAPSIBLE, so it costs only a header row until opened.
//     Six headers plus one open body is a very different budget from six open bodies.
//
// Why stack at all, when tabs already work: a tab strip shows six labels but admits only
// one at a time, and in a narrow panel the strip itself scrolls, so some labels are not
// even visible. A column of disclosures shows every heading at once, lets you open two
// together to compare, and never hides that the other five exist.

/** Roughly one collapsed disclosure header, including its rule. */
const HEADER_H = 30;

/**
 * Room the open section needs to be worth opening.
 *
 * Below this you get a column of headers and a body too short to read — strictly worse
 * than tabs, which at least give the one section the whole panel.
 */
const ENTER_BODY_H = 240;

/** Lower, so dragging a splitter across the boundary does not flip the layout each
 *  frame. Same reasoning as the dock's hysteresis. */
const LEAVE_BODY_H = 190;

export {HEADER_H, ENTER_BODY_H, LEAVE_BODY_H};

export function shouldStackTabs(args: {
    tabCount: number;
    heightPx: number;
    wasStacked: boolean;
}): boolean {
    const {tabCount, heightPx, wasStacked} = args;

    // One or two tabs are not a navigation problem; a strip of two is easier to read
    // than two headers with a rule between them.
    if (tabCount < 3) return false;

    const body = wasStacked ? LEAVE_BODY_H : ENTER_BODY_H;
    return heightPx >= tabCount * HEADER_H + body;
}
