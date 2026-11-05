// Enough of a browser for a panel to be server-rendered under `node --test`: the globals the store
// modules read at import, and a loader hook that answers vite's `?worker` / `?url` / `?raw` imports
// with stubs. Import it, then import the panel DYNAMICALLY: a static import is resolved before this
// module has run.

import { register } from "node:module";

const HOOKS = `
export async function load(url, context, next) {
  const m = /\\?(worker|url|raw)(&|$)/.exec(url);
  if (m) return { format: "module", source: m[1] === "worker" ? "export default class { addEventListener() {} removeEventListener() {} postMessage() {} terminate() {} }" : "export default ''", shortCircuit: true };
  return next(url, context);
}
`;
register("data:text/javascript," + encodeURIComponent(HOOKS));

const g = globalThis as Record<string, unknown>;
const cells = new Map<string, string>();
const storage = {
  getItem: (k: string) => (cells.has(k) ? cells.get(k)! : null),
  setItem: (k: string, v: string) => void cells.set(k, String(v)),
  removeItem: (k: string) => void cells.delete(k),
  clear: () => cells.clear(),
  key: () => null,
  length: 0,
};
g.window ??= {
  location: new URL("http://localhost/"),
  localStorage: storage,
  sessionStorage: storage,
  devicePixelRatio: 1,
  addEventListener() {},
  removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
g.localStorage ??= storage;
g.Worker ??= class { addEventListener() {} removeEventListener() {} postMessage() {} terminate() {} };
g.sessionStorage ??= storage;
g.document ??= {
  documentElement: { style: { setProperty() {} }, classList: { add() {}, remove() {} } },
  createElement: () => ({ style: {}, getContext: () => null }),
  addEventListener() {},
  removeEventListener() {},
};

/** Server rendering reads a zustand store's INITIAL state, so a test sets that one: `patch` is
 *  written into it (and the live state) for the rest of the file. */
export function setServerState<S extends object>(
  store: { getState: () => S; getInitialState: () => S; setState: (p: Partial<S>) => void },
  patch: Partial<S>,
): void {
  Object.assign(store.getInitialState(), patch);
  store.setState(patch);
}
