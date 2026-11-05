// Dispatches the registered shortcuts (`./shortcuts`) while mounted. Draws
// nothing. Keys typed into a field stay the field's, and a key a widget already
// handled (`defaultPrevented`) is left alone.

import {useEffect} from "react";

import {toggle} from "./reveal";
import {listShortcuts, matchShortcut} from "./shortcuts";

function isTextField(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

export function ShortcutKeys() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.repeat || isTextField(e.target)) return;
      const hit = matchShortcut(e, listShortcuts((id) => void toggle(id)));
      if (!hit) return;
      e.preventDefault();
      try {
        hit.run();
      } catch (err) {
        console.error(`[layout] shortcut "${hit.id}" threw`, err);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return null;
}
