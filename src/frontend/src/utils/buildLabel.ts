// The "Build:" line — what version this viewer is, and what produced it.
//
// Pure and separate from the component so the precedence below can be asserted.
// It is easy to get subtly wrong: every branch produces a plausible-looking
// string, so a mistake here does not fail, it just tells the reader something
// untrue about what they are running.

/** Which identifier goes in the parentheses, or `""` when nothing does.
 *
 *  Precedence, and the reason for it:
 *
 *  1. The frontend build-time git sha. The most precise answer available, so it
 *     wins whenever it exists.
 *  2. The deployed image's tag. A hosted viewer copies source rather than
 *     building from a checkout, so it has no build-time sha and the tag is the
 *     only provenance it carries.
 *
 *  A `sha-XXXXXXX` tag is unwrapped to the bare sha so both paths read alike.
 *
 *  ANY OTHER TAG IS KEPT, NOT DISCARDED. This is the case that was wrong: an
 *  image built from a release tag was rendering as the package version alone,
 *  because only `sha-` tags were recognised. That line cannot distinguish two
 *  images built from the same package release with different contents — which is
 *  precisely what an image assembled from independently-versioned components is,
 *  and precisely when somebody reading the build line needs to tell them apart.
 */
export function buildStamp(frontendSha: string, viewerImageTag: string): string {
    if (frontendSha) return frontendSha;
    const tag = (viewerImageTag || "").trim();
    if (!tag) return "";
    return tag.startsWith("sha-") ? tag.slice(4) : tag;
}

/** The whole line: `"<version> (<stamp>)"`, degrading as each half goes missing.
 *
 *  `uniqueVersionId` is the last resort — a build with neither a version nor any
 *  provenance still has to render something a bug report can quote. */
export function buildLabel(
    adapyVersion: string,
    frontendSha: string,
    viewerImageTag: string,
    uniqueVersionId: string | number,
): string {
    const stamp = buildStamp(frontendSha, viewerImageTag);
    if (adapyVersion) return stamp ? `${adapyVersion} (${stamp})` : adapyVersion;
    return stamp || String(uniqueVersionId);
}
