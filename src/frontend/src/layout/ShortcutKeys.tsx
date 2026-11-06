// Dispatches the registered shortcuts (`./shortcuts`) while mounted. Draws
// nothing; the listener itself is `./shortcutDispatch`'s (shared with a mounted
// viewer, so a key never fires twice).

import {useEffect} from "react";

import {toggle} from "./reveal";
import {installShortcutDispatcher} from "./shortcutDispatch";

export function ShortcutKeys() {
  useEffect(() => installShortcutDispatcher({toggle: (id) => void toggle(id)}), []);
  return null;
}
