// Subsequence filtering for lists people have to find one thing in.
//
// WHY NOT `includes`. The names this is written for are long, structured and
// share most of their characters: `ModelExportMain.rvm~AP400-STRU_MS`,
// `ModelExportTempSteel.rvm~AP400-STRU_TS`. A substring filter makes you type
// the separator and the case exactly, and "ap400ms" -- which is how someone
// actually thinks of that site -- matches nothing. A subsequence match finds it
// on the first three characters and keeps narrowing.
//
// WHY NOT A LIBRARY. This is a hundred lines against a dependency in a bundle
// that ships to every viewer, and the scoring is the part anyone would want to
// tune anyway.
//
// WHY THE ALIGNMENT IS SEARCHED RATHER THAN TAKEN GREEDILY, which is the whole
// reason this file is not thirty lines. A greedy matcher walks the query and
// takes the first position each character can occupy. On names with a long
// SHARED PREFIX that is not a mild inaccuracy, it is the difference between
// working and not:
//
//     query "elec" against  ModelExportMain.rvm~AP400-ELEC
//     greedy takes e,l,e from "ModelExport" and only then looks for c
//
// -- so every candidate matches through its prefix, every candidate scores
// within 0.1 of every other, and the ranking is noise. Measured on exactly the
// list above: 19.53, 19.51, 19.41, with `AP300-MECH` first. The list this
// serves is one entry per E3D SITE across a project's model files, which is
// hundreds of names all beginning `ModelExport`, so the case that breaks a
// greedy matcher is the only case there is.
//
// So it finds the BEST alignment, by dynamic programming over (query position,
// text position). That is O(query x text) per candidate -- a few thousand
// operations for a 40-character name -- and it runs on keystroke over a list of
// hundreds, which is nothing.
//
// THE SCORE EXISTS TO ORDER, NOT TO JUDGE. Every candidate that matches is
// shown; the score only decides which appears first.

/** Characters that begin a "word" in the names this filters: the separators an
 *  E3D path, a model file and a site name are built out of. A match just after
 *  one of these is what a person means by "starts with". */
const WORD_BREAK = /[\s\-_./~:()[\]]/;

/** Every matched character is worth this much, so a longer query always
 *  outranks a shorter one that matched by luck. */
const BASE = 1;
/** Landing at the start of a word. Deliberately larger than CONTIGUOUS: typing
 *  `elec` means the ELEC in the site name, not the `ele` inside `ModelExport`,
 *  and only this bonus can tell the two apart. */
const WORD_START = 12;
/** Directly after the previous match. What makes a run read as deliberate. */
const CONTIGUOUS = 8;
/** Charged once per gap, not per skipped character: a name is allowed to have
 *  structure between the parts you typed without being punished for its
 *  length. */
const GAP = 3;

export interface FuzzyMatch {
    /** Higher is better. Meaningless in absolute terms — only compare within
     *  one query's results. */
    score: number;
    /** Indices into the candidate that the query matched, in order. A UI can
     *  use them to highlight; nothing is obliged to. */
    positions: number[];
}

function charBonus(text: string, at: number): number {
    return BASE + (at === 0 || WORD_BREAK.test(text[at - 1]) ? WORD_START : 0);
}

/** Match `query` against `text` as a subsequence, case-insensitively, scoring
 *  the best alignment rather than the first.
 *
 *  Returns null when the query does not appear in order at all. An EMPTY query
 *  matches with score 0 and no positions, so a caller can filter with the same
 *  call it ranks with and not special-case the unfiltered list.
 */
export function fuzzyMatch(query: string, text: string): FuzzyMatch | null {
    const q = query.trim().toLowerCase();
    if (!q) return {score: 0, positions: []};

    const hay = text.toLowerCase();
    const n = hay.length;
    const m = q.length;
    if (m > n) return null;

    const NEG = -Infinity;
    // `best[j]` is the score of the best alignment of the query's first i+1
    // characters that ENDS at text position j. `from[j]` is where the previous
    // query character sat in that alignment, which is what makes the positions
    // recoverable without keeping every row.
    let prev = new Float64Array(n).fill(NEG);
    const rows: Int32Array[] = [];
    let curr = new Float64Array(n);

    for (let i = 0; i < m; i++) {
        curr = new Float64Array(n).fill(NEG);
        const back = new Int32Array(n).fill(-1);

        // Running best over everything strictly left of j-1, so the
        // "came from a gap" case is O(1) rather than a second loop.
        let gapBest = NEG;
        let gapFrom = -1;

        for (let j = 0; j < n; j++) {
            if (j >= 2) {
                const cand = prev[j - 2];
                if (cand > gapBest) {
                    gapBest = cand;
                    gapFrom = j - 2;
                }
            }
            if (hay[j] !== q[i]) continue;

            if (i === 0) {
                curr[j] = charBonus(text, j);
                back[j] = -1;
                continue;
            }
            // Contiguous with the previous character, or after a gap.
            const contiguous = j >= 1 && prev[j - 1] > NEG ? prev[j - 1] + CONTIGUOUS : NEG;
            const gapped = gapBest > NEG ? gapBest - GAP : NEG;
            if (contiguous === NEG && gapped === NEG) continue;

            if (contiguous >= gapped) {
                curr[j] = contiguous + charBonus(text, j);
                back[j] = j - 1;
            } else {
                curr[j] = gapped + charBonus(text, j);
                back[j] = gapFrom;
            }
        }

        rows.push(back);
        prev = curr;
    }

    let end = -1;
    let score = NEG;
    for (let j = 0; j < n; j++) {
        if (prev[j] > score) {
            score = prev[j];
            end = j;
        }
    }
    if (end < 0 || score === NEG) return null;

    const positions = new Array<number>(m);
    let j = end;
    for (let i = m - 1; i >= 0; i--) {
        positions[i] = j;
        j = rows[i][j];
    }

    // A short name that used most of itself to match is a better answer than a
    // long one that happened to contain the same letters.
    score += (m / Math.max(n, 1)) * 4;
    // Earlier is better, mildly: a hit in the stem beats one in a suffix every
    // name in the list shares.
    score -= Math.min(positions[0], 40) / 40;
    return {score, positions};
}

/** Filter and rank `items` by `query`, ties broken by `label` in the order a
 *  person reads names.
 *
 *  `localeCompare` with `numeric` is not a nicety here: these names are full of
 *  numbers, and a plain string sort puts `AP4000` before `AP400` and `10PL02`
 *  before `2PL01`, which reads as the list being in no order at all.
 */
export function fuzzyFilter<T>(items: T[], query: string, label: (item: T) => string): T[] {
    const collate = (a: string, b: string) => a.localeCompare(b, undefined, {numeric: true, sensitivity: "base"});

    if (!query.trim()) {
        return [...items].sort((a, b) => collate(label(a), label(b)));
    }

    const scored: {item: T; score: number; text: string}[] = [];
    for (const item of items) {
        const text = label(item);
        const hit = fuzzyMatch(query, text);
        if (hit) scored.push({item, score: hit.score, text});
    }
    scored.sort((a, b) => b.score - a.score || collate(a.text, b.text));
    return scored.map((s) => s.item);
}
