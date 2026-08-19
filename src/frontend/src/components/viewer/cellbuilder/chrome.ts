// The cellbuilder panel's shared class strings, lifted out of CellBuilderPanel so the
// split-out tab bodies can share them verbatim.
//
// These are the ORIGINAL strings, unchanged. They are ad-hoc chrome and they are on the
// noAdHocChrome allowlist; the re-chrome onto the design system is the second half of
// this split and deliberately not mixed into the move. Keeping them in one module at
// least means the re-chrome is one edit rather than five.

// Shared panel chrome uses the same CSS tokens as PANEL_CHROME (themeStore) but
// leaves padding/rounding to the pinned regions below.
export const CHROME =
  "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
  "text-[var(--ada-panel-text)] shadow-lg";
export const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
export const btnGray =
  "px-2 py-1 rounded-sm bg-gray-600 text-white disabled:opacity-50 hover:bg-gray-500";
export const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

export const FACE_LABELS = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"];
