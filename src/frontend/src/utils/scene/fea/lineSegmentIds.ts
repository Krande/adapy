// Which element each line SEGMENT belongs to.
//
// The first attempt at this kept a parallel array of segment ids and pushed to
// it under a condition ("did the vertex count just go even?"). It drifted: a
// highlight then named one beam and drew another, spanning two unrelated ends.
//
// So the label is recorded per VERTEX, in the same statement that pushes the
// vertex — alignment is structural rather than something a condition has to get
// right — and this derives the per-segment ids from that, refusing to guess
// when the two ends of a pair disagree.

/**
 * Collapse per-vertex labels into per-segment range ids.
 *
 * `vertexLabels` has one entry per emitted vertex, in emission order, two per
 * LineSegments segment. Returns `E<label>` per segment.
 *
 * Throws when a pair straddles two elements, or when the count is odd. Both
 * mean the caller's accounting is broken, and a wrong highlight is worse than a
 * missing one — a loud failure here is a caught bug, a silent one is the bug
 * this file exists because of.
 */
export function segmentRangeIds(vertexLabels: readonly number[]): string[] {
    if (vertexLabels.length % 2 !== 0) {
        throw new Error(
            `line segments: ${vertexLabels.length} vertices is not a whole number of ` +
            `segments; an incomplete pair was left behind`,
        );
    }
    const ids: string[] = new Array(vertexLabels.length / 2);
    for (let segment = 0; segment < ids.length; segment++) {
        const first = vertexLabels[segment * 2];
        const second = vertexLabels[segment * 2 + 1];
        if (first !== second) {
            throw new Error(
                `line segments: segment ${segment} pairs element ${first} with ` +
                `element ${second}; vertices are mis-paired`,
            );
        }
        ids[segment] = `E${first}`;
    }
    return ids;
}

/** Indices of the segments belonging to any of `selected`. */
export function selectedSegments(
    segmentIds: readonly string[],
    selected: Iterable<string>,
): number[] {
    const wanted = new Set(selected);
    if (wanted.size === 0) return [];
    const out: number[] = [];
    for (let segment = 0; segment < segmentIds.length; segment++) {
        if (wanted.has(segmentIds[segment])) out.push(segment);
    }
    return out;
}
