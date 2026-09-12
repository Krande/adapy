// Which scope the "Run audit" form should start on.
//
// A corpus is the release-gate flow, so when the deployment has one the form
// opens on it; the ad-hoc scopes stay available in the picker. The choice is
// only applied while the operator has not touched the picker, so a scope they
// selected before the corpora finished loading is never overwritten.

import type {Corpus} from "@/services/viewerApi";

export const AD_HOC_DEFAULT_SCOPE = "shared";

export function defaultAuditScope(corpora: readonly Pick<Corpus, "slug">[]): string {
    const first = corpora.find((c) => Boolean(c.slug));
    return first ? `corpus:${first.slug}` : AD_HOC_DEFAULT_SCOPE;
}
