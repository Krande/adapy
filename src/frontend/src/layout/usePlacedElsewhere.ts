// Re-render a plugin region host when the layout moves one of its panels to a
// place of its own, or back (`./placedElsewhere`).

import { useSyncExternalStore } from "react";

import { placedElsewhereVersion, subscribePlacedElsewhere } from "./placedElsewhere";

export function usePlacedElsewhereVersion(): number {
  return useSyncExternalStore(subscribePlacedElsewhere, placedElsewhereVersion, placedElsewhereVersion);
}
