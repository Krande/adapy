// Keep a consumed deep link alive for a reloading UI-shell switch.
//
// `useUrlParamLoad` loads `?scope&file&derived` and then strips the whole query
// string, so a manual reload does not load the file again. Most shell switches
// happen in place and keep the scene as it is; the ones that reload
// (`setActiveUiShell`: a shell with a root of its own) boot a page with nothing
// to load, and the model on screen would disappear. This puts the stripped
// params back into that switch's reload URL, for as long as the deep-linked
// file is what the page shows.
//
// Free of React and the stores (the caller passes `isLoaded`), so it runs under
// plain `node --test`.

import { carryOnShellSwitch, UI_SHELL_URL_PARAM } from "@/plugins/uiShells";

export interface DeepLinkCarry {
  /** The deep-linked load has finished, or given up. From now on the params are
   *  carried only while `isLoaded()` says the file is in the scene. */
  settle(): void;
  /** Stop carrying the params. */
  dispose(): void;
}

/**
 * Carry the params of `search` (a deep link's query string, as it was before
 * it was stripped) into the next reloading shell switch. Every param is
 * carried, not only `file`: `scope` and `derived` say where the file comes
 * from, and the rest may be plugin url-param handlers' (they are stripped with
 * the others). `ui` is not: the switch is choosing the shell.
 *
 * While the load is still running the params are carried regardless; once it
 * has settled, only while `isLoaded()` holds, so a model the user has since
 * closed does not come back with the other shell.
 */
export function carryDeepLinkOnShellSwitch(search: string, isLoaded: () => boolean): DeepLinkCarry {
  const params: Record<string, string> = {};
  for (const [key, value] of new URLSearchParams(search)) {
    if (key !== UI_SHELL_URL_PARAM) params[key] = value;
  }
  let settled = false;
  const dispose = carryOnShellSwitch(() => (!settled || isLoaded() ? params : null));
  return {
    settle: () => {
      settled = true;
    },
    dispose,
  };
}
