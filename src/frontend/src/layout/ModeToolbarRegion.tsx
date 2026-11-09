// The region of a header a mode's toolbars live in. Core owns it so every composed shell gets the
// same behaviour: the region takes the room that is left (`flex: 1`, `min-width: 0`), the toolbars
// sit left-aligned in it, and when they do not fit they are clipped behind a "»" button that opens
// them in a wrapping popover -- they never push what sits beside the region (the mode switcher
// stays where it is whichever mode is active).

import React from "react";

const POPOVER =
  "absolute right-0 top-full z-50 flex max-w-[90vw] flex-wrap items-center gap-1 rounded-sm border p-1 shadow-lg " +
  "border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-header-bg,#1f2937)] text-[var(--ada-window-header-text,#f3f4f6)]";

/** Does `el` hold more than it shows (sideways)? */
export const overflows = (el: {scrollWidth: number; clientWidth: number}): boolean => el.scrollWidth > el.clientWidth + 1;

export const ModeToolbarRegion: React.FC<{children?: React.ReactNode; className?: string}> = ({children, className = ""}) => {
  const clip = React.useRef<HTMLDivElement>(null);
  const [over, setOver] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  React.useLayoutEffect(() => {
    const el = clip.current;
    if (!el) return;
    const measure = () => setOver(overflows(el));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    // The content changes with the mode: watch what is inside, too.
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  });
  React.useEffect(() => {
    if (!over) setOpen(false);
  }, [over]);
  return (
    <div className={`relative flex min-w-0 flex-1 items-center ${className}`} data-mode-toolbar-region="">
      <div ref={clip} className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden" data-mode-toolbar-clip="">
        <div className="flex shrink-0 items-center gap-2">{children}</div>
      </div>
      {over && (
        <button
          type="button"
          className="ml-1 shrink-0 rounded-sm px-1.5 py-0.5 text-xs hover:bg-white/10"
          aria-label="More tools"
          title="More tools"
          aria-expanded={open}
          data-mode-toolbar-more=""
          onClick={() => setOpen((v) => !v)}
        >
          »
        </button>
      )}
      {over && open && (
        <div className={POPOVER} role="group" aria-label="More tools" data-mode-toolbar-popover="">
          {children}
        </div>
      )}
    </div>
  );
};
