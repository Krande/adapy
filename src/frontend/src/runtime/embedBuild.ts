// Whether this code runs in the ada-viewer embed bundle (paradoc's 3D figures), not the app.
//
// vite.config.embed.ts defines `__ADA_EMBED__` as true; every other build and the tests (plain
// Node, no Vite) leave it undefined, hence the typeof guard.

declare const __ADA_EMBED__: boolean | undefined;

export const IS_EMBED_BUILD: boolean = typeof __ADA_EMBED__ !== "undefined" && __ADA_EMBED__ === true;
