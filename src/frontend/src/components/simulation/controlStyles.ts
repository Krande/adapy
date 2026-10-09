// The Simulation panel's control looks, in one place: the panel and its export button draw
// the same buttons and fields, and a style kept in each file drifted apart.

/** Dropdowns and number fields: one height, so a row of them lines up. */
export const FIELD_CONTROL = "text-black bg-white rounded-sm px-1 py-0.5";

/** The playback row's icon buttons: compact and all one size. */
export const TRANSPORT_BUTTON =
    "h-8 w-9 shrink-0 grid place-items-center rounded-md bg-blue-700 text-white hover:bg-blue-600 " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300";

/** A transport button whose panel is open. */
export const TRANSPORT_BUTTON_ON = "ring-2 ring-blue-300";
