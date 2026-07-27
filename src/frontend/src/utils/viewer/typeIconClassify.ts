import type { TypePortSummary } from "@/services/viewerApi";

// Pure classification for the type-icon overlay: map an equipment archetype to
// an icon kind, a system (type + medium) to a fluid/service marker, and list an
// equipment's still-unconnected input ports.

export type EquipIcon = "electrical" | "pump" | "tank" | "other";
export type MediumMarker = "water" | "oil" | "electrical" | "duct" | "generic";

/** An equipment's icon kind, from its archetype slug and port summary. An OUT
 * electrical port marks a producer/switchboard even if the slug is custom. */
export function classifyEquipment(
  slug: string | undefined,
  ports: TypePortSummary[] | undefined,
): EquipIcon {
  const s = (slug ?? "").toLowerCase();
  const hasElecOut = (ports ?? []).some(
    (p) =>
      p.category === "electrical" &&
      (p.direction === "OUT" || p.direction === "INOUT"),
  );
  if (
    hasElecOut ||
    /switchboard|board|generator|transformer|genset|breaker|switchgear/.test(s)
  )
    return "electrical";
  if (/pump|compressor|blower/.test(s)) return "pump";
  if (/tank|vessel|drum|separator|silo/.test(s)) return "tank";
  return "other";
}

/** The fluid/service marker for a system run, from its base kind + medium. */
export function classifyMedium(
  type: string,
  medium: string | null | undefined,
): MediumMarker {
  const m = (medium ?? "").toLowerCase();
  if (type === "electrical" || type === "cable") return "electrical";
  if (type === "duct") return "duct";
  if (/oil|diesel|fuel|hydrocarbon|crude|lube/.test(m)) return "oil";
  if (/water|cooling|sea|fresh|glycol|condensate/.test(m)) return "water";
  return "generic";
}

/** Names of an equipment's input ports (direction IN). */
export function inputPortNames(ports: TypePortSummary[] | undefined): string[] {
  return (ports ?? []).filter((p) => p.direction === "IN").map((p) => p.name);
}

/** Input ports of `equipmentName` not connected by any system — the missing
 * inputs surfaced as a red badge. `connected` is the set of
 * `"<equipment>::<port>"` keys across all system connections. */
export function missingInputs(
  equipmentName: string,
  ports: TypePortSummary[] | undefined,
  connected: Set<string>,
): string[] {
  return inputPortNames(ports).filter(
    (port) => !connected.has(`${equipmentName}::${port}`),
  );
}
