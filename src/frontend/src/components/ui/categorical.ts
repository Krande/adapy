/**
 * Colours that mean "these are different", not "this is good/bad/interactive".
 *
 * The semantic tokens (accent, pass, warn, fail, surface) answer "what does this colour
 * SAY". A branch chip, a per-series line, a legend swatch answers something else: it only
 * has to be reliably distinguishable from its neighbours, and stable for the same input
 * across reopens. Forcing that through semantic tokens would either wash every category
 * the same colour or abuse "fail red" to mean "the third branch".
 *
 * So raw hues are correct here — and belong in ONE place. Scattered through feature files
 * they read as exactly the ad-hoc chrome `noAdHocChrome` exists to stamp out, which is why
 * this file lives in `ui/` (the one directory that rule exempts) instead of as a
 * permanent allowlist entry apologising for itself.
 *
 * Mid-700 weights throughout: dark enough for white text at chip size in the light theme,
 * light enough to stay legible against the dark panel surfaces.
 */
export const CATEGORICAL_CHIPS = [
    "bg-pass",
    "bg-sky-700",
    "bg-violet-700",
    "bg-warn",
    "bg-rose-700",
    "bg-teal-700",
    "bg-indigo-700",
] as const;

/**
 * A stable chip colour for an arbitrary key.
 *
 * Deterministic: the same branch gets the same colour every time the panel is reopened,
 * which is the whole point — a colour that shuffles between sessions is decoration, not
 * information. Cheap string hash, `| 0` to keep it in int32, `Math.abs` because the hash
 * can go negative and a negative modulus would index off the front of the array.
 */
export function categoricalChip(key: string): string {
    let h = 0;
    for (let i = 0; i < key.length; i++) {
        h = (h * 31 + key.charCodeAt(i)) | 0;
    }
    return CATEGORICAL_CHIPS[Math.abs(h) % CATEGORICAL_CHIPS.length];
}
