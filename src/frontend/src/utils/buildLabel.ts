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

/** The adapy ref this image was built from, when it is worth saying.
 *
 *  Worth saying means: NOT the release the version already implies. A viewer
 *  built from `v0.64.1` reporting version `0.64.1` says the same thing twice,
 *  so that case renders nothing and the line stays short.
 *
 *  THE CASE THIS EXISTS FOR is the opposite one, and it is invisible without
 *  it. A branch cut from `v0.64.1` with no version bump ALSO reports `0.64.1`,
 *  and the image tag is the assembling repo's commit and run number — so a
 *  viewer built from a feature branch and one built from the release render
 *  identically, down to the character. There is no way to tell them apart from
 *  the running deployment; the answer exists only in the inputs of whichever CI
 *  run built it. That has already caused one wrong conclusion about what was
 *  deployed.
 */
export function adapyRefStamp(adapyVersion: string, adapyRef: string): string {
    const ref = (adapyRef || "").trim();
    if (!ref) return "";
    const version = (adapyVersion || "").trim();
    // The ref may carry the commit it resolved to, as `<ref>@<sha>` — a branch
    // moves, so the name alone dates badly. Only the NAME is compared here: a
    // release build passing `v0.64.1@abc1234` still says nothing the version
    // does not, and appending the sha must not be what makes it start showing.
    const named = ref.split("@")[0];
    if (version && (named === version || named === `v${version}`)) return "";
    return ref;
}

/** The whole line: `"<version> (<stamp>)"`, degrading as each half goes missing.
 *
 *  `uniqueVersionId` is the last resort — a build with neither a version nor any
 *  provenance still has to render something a bug report can quote.
 *
 *  `adapyRef` is appended inside the parentheses when it says something the
 *  version does not — see `adapyRefStamp`. It is last because it is the rarest:
 *  most builds are release builds, where it renders nothing at all. */
export function buildLabel(
    adapyVersion: string,
    frontendSha: string,
    viewerImageTag: string,
    uniqueVersionId: string | number,
    adapyRef: string = "",
): string {
    const stamp = buildStamp(frontendSha, viewerImageTag);
    const ref = adapyRefStamp(adapyVersion, adapyRef);
    // Inside the parentheses rather than after them: it qualifies the build,
    // and a reader quoting "the bit in brackets" into a bug report should be
    // quoting the whole answer.
    const inner = stamp && ref ? `${stamp}, adapy ${ref}` : stamp || (ref && `adapy ${ref}`);
    if (adapyVersion) return inner ? `${adapyVersion} (${inner})` : adapyVersion;
    return inner || String(uniqueVersionId);
}
