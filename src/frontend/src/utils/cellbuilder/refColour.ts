// The colour a plugin's ref (state/cellbuilder/refsSlice.ts) is drawn in: its own, or the
// builder's ref colour -- violet, apart from every cell kind. One place, so the scene and the
// lists agree.

import type {BuilderRef} from "@/state/cellbuilder/refsSlice";

export const REF_DEFAULT_COLOUR = "#a855f7";

/** The ref's colour as CSS. */
export const refColourCss = (ref: Pick<BuilderRef, "color">): string => ref.color || REF_DEFAULT_COLOUR;
