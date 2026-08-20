import {buttonClasses, fieldClasses} from "@/components/ui";

// The cellbuilder panel's shared class strings.
//
// These used to be hand-written Tailwind palette classes — three of the ~12 recipes the
// M0 audit found for the same three visual roles. They are now the design system's own classes, obtained from
// the same functions <Button> and <Input> use, so there is exactly one definition of
// what a primary button looks like and the cellbuilder cannot drift from it.
//
// Why classes and not the components: these roles are applied to several hundred dense
// tool rows whose elements carry their own refs, aria wiring, menu anchors and split
// borders. Swapping each for <Button> is a rewrite, not a re-chrome, and phase 1 of this
// split exists precisely so the two are not mixed. New code here should use <Button> and
// <Input> directly.

/** The one call to action in a region — Compile, Add, Commit. */
export const btn = buttonClasses("primary", "sm");
/** The workhorse. Named `btnGray` for its call sites; it is the secondary variant. */
export const btnGray = buttonClasses("secondary", "sm");
/** Text/number/select fields. */
export const inputCls = fieldClasses("sm");

export const FACE_LABELS = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"];

/** The panel wrapper for the classic UI's floating/bottom-sheet frame. Padding and
 *  rounding stay with the pinned regions that use it. */
export const CHROME = "bg-surface-1 border border-edge text-content shadow-float";
