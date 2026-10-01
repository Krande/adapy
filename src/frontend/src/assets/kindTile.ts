// A kind's tile, for the "tiles" tree style: two letters and a colour, derived
// from the kind string alone.
//
// Core does not know any provider's vocabulary, so it cannot hand-pick a colour
// per kind. It hashes the kind instead: the same kind gets the same tile in
// every collection and every session, and nothing here names a kind.

/** Muted hues that read on the tab's dark ground and differ in lightness as
 *  well as hue, so neighbours stay apart for colour-blind readers. */
const PALETTE: readonly { fg: string; bg: string }[] = [
  { fg: "#8fbaff", bg: "rgba(111,168,255,.16)" },
  { fg: "#72d8b8", bg: "rgba(95,201,168,.18)" },
  { fg: "#e8bd6a", bg: "rgba(224,179,92,.16)" },
  { fg: "#d2a6f0", bg: "rgba(199,146,234,.16)" },
  { fg: "#8fd9ec", bg: "rgba(127,208,230,.14)" },
  { fg: "#f4a08d", bg: "rgba(240,143,122,.15)" },
  { fg: "#f0a6c8", bg: "rgba(236,140,186,.15)" },
  { fg: "#b8d982", bg: "rgba(170,210,110,.15)" },
];

export interface KindTile {
  readonly letters: string;
  readonly fg: string;
  readonly bg: string;
}

/** FNV-1a over the normalised kind: stable, cheap, and well spread over eight. */
function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export function kindTile(kind: string): KindTile {
  const k = kind.trim().toLowerCase();
  const { fg, bg } = PALETTE[hash(k) % PALETTE.length];
  // Two letters, because one collides at once in any real vocabulary
  // (site / stru / sbfr): "Si", "St", "Sb" stay apart where "S" would not.
  const letters = k ? k.charAt(0).toUpperCase() + k.slice(1, 2) : "·";
  return { letters, fg, bg };
}
