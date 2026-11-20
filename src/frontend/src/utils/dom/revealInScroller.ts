// Bring an element into view by scrolling the one box it scrolls in, and nothing else.
//
// `Element.scrollIntoView` scrolls EVERY scrollable ancestor up to the document, an
// `overflow: hidden` one too. The viewer's panels sit in a box over the 3D view inside a page that
// does not scroll (`html, body { overflow: hidden }`): a joint row of the Connections panel brought
// into view that way scrolled the hidden page, and the top bar went off the top of the screen with
// no way back. This scrolls only the nearest ancestor that scrolls on its own (`overflow-y` auto or
// scroll), as far as needed to show the element ("nearest") or to centre it ("center").

const SCROLLS = /(auto|scroll|overlay)/;

/** The nearest ancestor of `el` that scrolls vertically on its own, or null. */
export function scrollParentOf(el: Element): HTMLElement | null {
    for (let p = el.parentElement; p; p = p.parentElement) {
        const style = typeof getComputedStyle === "function" ? getComputedStyle(p) : null;
        if (style && SCROLLS.test(style.overflowY) && p.scrollHeight > p.clientHeight) return p;
    }
    return null;
}

/** The scrollTop that shows `[top, top + height)` (relative to the box's content) in a box of
 *  `viewHeight` scrolled to `current`. */
export function scrollTopFor(
    current: number,
    viewHeight: number,
    top: number,
    height: number,
    block: "nearest" | "center",
): number {
    if (block === "center") return Math.max(0, top + height / 2 - viewHeight / 2);
    if (top < current) return Math.max(0, top);
    if (top + height > current + viewHeight) return Math.max(0, Math.min(top, top + height - viewHeight));
    return current;
}

/** Scroll `el`'s own scroll box so that `el` shows; nothing outside that box moves. */
export function revealInScroller(el: Element | null | undefined, block: "nearest" | "center" = "nearest"): void {
    if (!el) return;
    const box = scrollParentOf(el);
    if (!box) return;
    const boxRect = box.getBoundingClientRect();
    const rect = el.getBoundingClientRect();
    const top = rect.top - boxRect.top + box.scrollTop;
    const next = scrollTopFor(box.scrollTop, box.clientHeight, top, rect.height, block);
    if (next !== box.scrollTop) box.scrollTop = next;
}
