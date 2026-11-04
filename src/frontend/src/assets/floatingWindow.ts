// A floating window's geometry, the pure half: where it opens, how it is kept on screen, and how a
// remembered position is read back.
//
// KEPT ON SCREEN, NOT KEPT AS SAVED. A position remembered on a large monitor can be off a laptop's
// screen; a window resized below its minimum by a hand edit is unusable. So whatever comes from
// storage, or from a viewport that shrank, goes through `clampRect` first: the size is bounded by
// the minimum and the viewport, the position so the whole window -- its title bar above all -- is
// inside it. A viewport smaller than the minimum gets the viewport, not a window that overflows it.
//
// React-free and storage-free, so it runs under `node --test`.

export interface WindowRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Extent {
  width: number;
  height: number;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** `r` resized into [min, viewport] and moved so it lies wholly inside the viewport. */
export function clampRect(r: WindowRect, viewport: Extent, min: Extent): WindowRect {
  const vw = Math.max(0, Math.floor(viewport.width));
  const vh = Math.max(0, Math.floor(viewport.height));
  const width = Math.round(Math.min(vw, Math.max(min.width, r.width)));
  const height = Math.round(Math.min(vh, Math.max(min.height, r.height)));
  const x = Math.round(Math.min(Math.max(0, r.x), vw - width));
  const y = Math.round(Math.min(Math.max(0, r.y), vh - height));
  return { x, y, width, height };
}

/** Where a window opens the first time: `preferred` size, centred across, near the top. */
export function defaultRect(viewport: Extent, preferred: Extent, min: Extent, top = 60): WindowRect {
  const width = Math.min(preferred.width, viewport.width - 32);
  const height = Math.min(preferred.height, viewport.height - top - 20);
  return clampRect({ x: (viewport.width - width) / 2, y: top, width, height }, viewport, min);
}

/**
 * A window centred both ways at `preferred` size, kept `margin` px from the
 * viewport's sides and at least `top` px from its top. Where the Simulation
 * panel's floating dialog opens.
 */
export function centredRect(
  viewport: Extent,
  preferred: Extent,
  margin: { x: number; y: number; top: number },
): WindowRect {
  const width = Math.min(preferred.width, viewport.width - 2 * margin.x);
  const height = Math.min(preferred.height, viewport.height - 2 * margin.y);
  return {
    x: Math.max(margin.x, (viewport.width - width) / 2),
    y: Math.max(margin.top, (viewport.height - height) / 2),
    width,
    height,
  };
}

/** `start` moved by a pointer delta, never past the viewport's top-left corner. */
export function moveRect(start: WindowRect, dx: number, dy: number): WindowRect {
  return { x: Math.max(0, start.x + dx), y: Math.max(0, start.y + dy), width: start.width, height: start.height };
}

/** `start` resized from its bottom-right corner by a pointer delta, never below `min`. */
export function resizeRect(start: WindowRect, dx: number, dy: number, min: Extent): WindowRect {
  return {
    x: start.x,
    y: start.y,
    width: Math.max(min.width, start.width + dx),
    height: Math.max(min.height, start.height + dy),
  };
}

/** A rect read back from storage, or null when it is not one (absent, a hand edit, an older shape). */
export function parseStoredRect(raw: string | null): WindowRect | null {
  if (!raw) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  if (!finite(o.x) || !finite(o.y) || !finite(o.width) || !finite(o.height)) return null;
  if (o.width <= 0 || o.height <= 0) return null;
  return { x: o.x, y: o.y, width: o.width, height: o.height };
}
