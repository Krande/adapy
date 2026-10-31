// What the Sources tab offers a viewer, by role -- one rule, read by every panel of the tab.
//
// Provider data is kept current by an admin: the scheduled sweeps (and apply-changes) publish it
// into the provider store, and a member reads what their scope is GRANTED of it. So a member's
// Sources tab is the granted collections and their data -- the tree, attributes, search, loading,
// saved groups and sets -- and nothing that drives a provider or handles what one staged:
//
// * requests: a collection request, a node's or a saved group's geometry request;
// * provider options: how a provider opens a collection (an admin setting, sent with its jobs);
// * staging: what a provider job staged and nobody published (publish or delete it);
// * files: the scope's raw asset files (legacy per-scope copies, deletable).
//
// The server refuses the provider jobs to members on its own; this only stops offering them.

export interface SourcesTabAccess {
    /** Request a collection, or a node's / saved group's geometry, from a provider. */
    requests: boolean;
    /** The per-collection provider options panel. */
    providerOptions: boolean;
    /** "Staged, not published", with its publish and delete buttons. */
    staging: boolean;
    /** The scope's raw asset files. */
    files: boolean;
}

export function sourcesTabAccess(isAdmin: boolean): SourcesTabAccess {
    return { requests: isAdmin, providerOptions: isAdmin, staging: isAdmin, files: isAdmin };
}
