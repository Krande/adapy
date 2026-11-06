// The zone keys of `./zoneKeys` in the page: F6 / Shift+F6 between the zones a
// preset marks with `ZONE_ATTR`, Esc on a floating window, and focus that returns to where it came from when a window goes away.

import React, {useEffect, useLayoutEffect} from "react";

import type {PanelId} from "./panel";
import {
  PANEL_ACTIONS_ATTR,
  PANEL_TOGGLE_ATTR,
  ZONE_ATTR,
  ZONE_PANEL_ATTR,
  cycleZone,
  orderZones,
  zoneKeyIntent,
} from "./zoneKeys";

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const shown = (el: Element): boolean => el.getClientRects().length > 0;

/** Focus the first focusable element in `zone`, else the zone itself when it takes focus. */
export function focusZone(zone: Element | null): boolean {
  if (!(zone instanceof HTMLElement)) return false;
  const first = Array.from(zone.querySelectorAll<HTMLElement>(FOCUSABLE)).find(shown);
  const target = first ?? (zone.hasAttribute("tabindex") ? zone : null);
  if (!target) return false;
  target.focus();
  return document.activeElement === target;
}

function zones(): HTMLElement[] {
  const all = Array.from(document.querySelectorAll<HTMLElement>(`[${ZONE_ATTR}]`)).filter(shown);
  return orderZones(all, (z) => z.getAttribute(ZONE_ATTR));
}

function floatOf(el: Element | null): HTMLElement | null {
  return (el?.closest?.(`[${ZONE_ATTR}="float"]`) as HTMLElement | null) ?? null;
}

function isTextField(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

function cycle(dir: 1 | -1): void {
  const list = zones();
  let i = list.findIndex((z) => z.contains(document.activeElement));
  for (let tries = 0; tries < list.length; tries++) {
    i = cycleZone(i, list.length, dir);
    if (focusZone(list[i])) return;
  }
}

/**
 * Where focus goes when the window of `panelId` closes while it has focus: the
 * panel's own header if it is docked again, else what had focus before the
 * window opened, else the panel's top-bar toggle, else the top bar.
 */
export function restoreFocus(panelId: PanelId, opener: HTMLElement | null): void {
  const esc = (s: string) => (typeof CSS !== "undefined" && CSS.escape ? CSS.escape(s) : s.replace(/"/g, '\\"'));
  const docked = document.querySelector(`[${PANEL_ACTIONS_ATTR}="${esc(panelId)}"]`);
  if (docked && focusZone(docked)) return;
  if (opener?.isConnected && shown(opener)) {
    opener.focus();
    if (document.activeElement === opener) return;
  }
  const toggle = document.querySelector<HTMLElement>(`[${PANEL_TOGGLE_ATTR}="${esc(panelId)}"]`);
  if (toggle && shown(toggle)) {
    toggle.focus();
    return;
  }
  focusZone(zones()[0] ?? null);
}

const focusRequests = new Set<PanelId>();

/**
 * Move focus into the window of `panelId` when it next opens. For an action
 * whose own button leaves the page with it (Float in a docked header, a tray chip).
 */
export function requestFloatFocus(panelId: PanelId): void {
  focusRequests.add(panelId);
}

/**
 * A floating window's focus: moved into the window when it opens on the user's
 * action (not when a reload restores it), and handed back by `restoreFocus`
 * when it goes away while holding it.
 */
export function useFloatFocus(panelId: PanelId, ref: React.RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const active = document.activeElement;
    const opener = active instanceof HTMLElement && active !== document.body ? active : null;
    const requested = focusRequests.delete(panelId);
    if (requested || (opener && !ref.current?.contains(opener))) focusZone(ref.current);
    return () => {
      // Runs before the window's nodes leave the page.
      const had = !!ref.current && ref.current.contains(document.activeElement);
      if (had) requestAnimationFrame(() => restoreFocus(panelId, opener));
    };
    // Once per window: the panel id is its key.
  }, []);
}

export interface ZoneKeysProps {
  closeFloat(panelId: PanelId): void;
}

/** Listens for the zone keys while mounted. Draws nothing. */
export function ZoneKeys({closeFloat}: ZoneKeysProps) {
  useEffect(() => {
    // F6 in the capture phase, so a focused widget that keeps its keys does not swallow it.
    const onCapture = (e: KeyboardEvent) => {
      if (e.key !== "F6") return;
      const intent = zoneKeyIntent(e, {inFloat: false, inTextField: false, overlayOpen: false});
      if (!intent) return;
      e.preventDefault();
      cycle(intent === "next-zone" ? 1 : -1);
    };
    // Esc bubbles, so a widget inside the window handles it first.
    const onBubble = (e: KeyboardEvent) => {
      if (e.key === "F6") return;
      const active = document.activeElement;
      const win = floatOf(active);
      const intent = zoneKeyIntent(e, {
        inFloat: !!win,
        inTextField: isTextField(active),
        overlayOpen: !!document.querySelector('[aria-modal="true"], [role="menu"]'),
      });
      const panelId = win?.getAttribute(ZONE_PANEL_ATTR);
      if (intent !== "close-float" || !panelId) return;
      e.preventDefault();
      closeFloat(panelId);
    };
    window.addEventListener("keydown", onCapture, true);
    window.addEventListener("keydown", onBubble);
    return () => {
      window.removeEventListener("keydown", onCapture, true);
      window.removeEventListener("keydown", onBubble);
    };
  }, [closeFloat]);
  return null;
}
