// The layout-engine flag: render the classic UI through the layout host.
//
// Off by default, and with it off the classic UI is the hand-written tree it has
// always been (`app.tsx`'s `AppBody`, `Menu.tsx`). On, the same UI is rendered
// from the panel registry by `LayoutHost` with the classic preset -- which must
// produce exactly the same DOM. The flag exists so that can be verified, and
// used, side by side before the hand-written tree is retired.
//
//   ?layout=engine     on for this page load
//   ?layout=classic    off for this page load
//   localStorage["ada:layout-engine"] = "on"   on until removed
//
// The URL wins over storage, so a link can force either path. Dependency-free:
// the location and the storage are passed in (or read from the browser).

export const LAYOUT_ENGINE_PARAM = "layout";
export const LAYOUT_ENGINE_STORAGE_KEY = "ada:layout-engine";

/** The part of `Storage` the flag reads. */
export interface FlagStorage {
  getItem(key: string): string | null;
}

const ON = new Set(["engine", "on", "1", "true"]);
const OFF = new Set(["classic", "off", "0", "false"]);

function browserSearch(): string {
  return typeof window !== "undefined" ? window.location.search : "";
}

function browserStorage(): FlagStorage | null {
  try {
    return typeof globalThis.localStorage !== "undefined" ? globalThis.localStorage : null;
  } catch {
    return null; // storage blocked
  }
}

/** Is the layout engine on: the URL's `layout` parameter, else the stored flag, else off. */
export function layoutEngineEnabled(
  search: string = browserSearch(),
  storage: FlagStorage | null = browserStorage(),
): boolean {
  const param = new URLSearchParams(search).get(LAYOUT_ENGINE_PARAM)?.trim().toLowerCase();
  if (param && ON.has(param)) return true;
  if (param && OFF.has(param)) return false;
  let stored: string | null = null;
  try {
    stored = storage?.getItem(LAYOUT_ENGINE_STORAGE_KEY) ?? null;
  } catch {
    stored = null;
  }
  return !!stored && ON.has(stored.trim().toLowerCase());
}
