// Scene overlays: what core draws over loaded models from the relations index.
//
// An overlay is a CORE entity: core names it, says which relations it reads, and turns them into
// marks. Providers never ship one -- they populate it by publishing the relations it reads
// (`ada.assets/node-relations@1`), so a provider that publishes `flows_to` lights up the flow
// overlay with no viewer code of its own.
//
// Marks are placed from the scene's own geometry (each element's drawn bounds), not from positions
// in a relation's props: the scene frame is the loader's, and every provider's frame differs.
//
// React-free, store-free and three-free, so it runs under `node --test`.

import type { NodeRelation } from "@/services/api/nodeRelations";
import type { Aabb } from "@/utils/bboxSelection/boxMath";

export type Vec3 = [number, number, number];

export type OverlayId = "systems" | "flow" | "penetrations" | "connections";

export interface OverlaySpec {
  readonly id: OverlayId;
  readonly label: string;
  readonly description: string;
  /** The relations it reads. */
  readonly relations: readonly string[];
}

export const OVERLAYS: readonly OverlaySpec[] = [
  {
    id: "systems",
    label: "Systems",
    description: "Elements outlined in their system's colour (serves_system).",
    relations: ["serves_system"],
  },
  {
    id: "flow",
    label: "Flow direction",
    description: "An arrow at each joint of a run, pointing downstream (flows_to).",
    relations: ["flows_to"],
  },
  {
    id: "penetrations",
    label: "Penetrations",
    description: "Pieces that fill an opening or pass through an element, linked to it.",
    relations: ["fills_opening", "penetrates"],
  },
  {
    id: "connections",
    label: "Connections",
    description: "A line between elements joined to each other (connects_to).",
    relations: ["connects_to"],
  },
];

export const OVERLAY_RELATIONS: readonly string[] = [...new Set(OVERLAYS.flatMap((o) => o.relations))];

/** An element's drawn bounds by its full name; null when it is not drawn in the scene. */
export type BoundsOf = (fullName: string) => Aabb | null;

export interface SystemEntry {
  name: string;
  kind: string | null;
  colour: string;
  members: string[];
}

export interface Arrow {
  from: Vec3;
  to: Vec3;
  /** `flow` when the provider knows the medium's direction; `modelled` for the run's order only. */
  basis: "flow" | "modelled";
}

export interface Link {
  from: Vec3;
  to: Vec3;
}

export interface OverlayMarks {
  systems: { entries: SystemEntry[]; boxes: { box: Aabb; colour: string }[] };
  flow: { arrows: Arrow[]; flowKnown: number; modelledOnly: number };
  penetrations: { boxes: Aabb[]; links: Link[] };
  connections: { links: Link[] };
}

/** A fixed, distinguishable palette; a system keeps its colour while the scene is unchanged. */
const PALETTE = [
  "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46f0f0", "#f032e6", "#bcf60c",
  "#fabebe", "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000", "#aaffc3", "#808000",
]; // prettier-ignore

export function centre(b: Aabb): Vec3 {
  return [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2];
}

const target = (r: NodeRelation): string => String(r.to_full_name ?? r.to_node_id ?? "");

/** Every overlay's marks from the relations of everything loaded, in one pass so a system keeps
 *  one colour across models. Relations whose element is not drawn are skipped; a target that is
 *  not drawn costs the link, not the element's own mark. A member counted twice (the same relation
 *  from two providers) is drawn once. */
export function overlayMarks(relations: readonly NodeRelation[], boundsOf: BoundsOf): OverlayMarks {
  const marks: OverlayMarks = {
    systems: { entries: [], boxes: [] },
    flow: { arrows: [], flowKnown: 0, modelledOnly: 0 },
    penetrations: { boxes: [], links: [] },
    connections: { links: [] },
  };
  const systems = new Map<string, SystemEntry>();
  const pierced = new Set<string>();
  for (const r of relations) {
    const from = r.from_full_name ? boundsOf(r.from_full_name) : null;
    if (!from || !r.from_full_name) continue;
    const to = target(r);
    switch (r.relation) {
      case "serves_system": {
        let entry = systems.get(to);
        if (!entry) {
          const kind = typeof r.props.system_kind === "string" ? r.props.system_kind : null;
          entry = { name: to, kind, colour: PALETTE[systems.size % PALETTE.length], members: [] };
          systems.set(to, entry);
        }
        if (!entry.members.includes(r.from_full_name)) {
          entry.members.push(r.from_full_name);
          marks.systems.boxes.push({ box: from, colour: entry.colour });
        }
        break;
      }
      case "flows_to": {
        const next = boundsOf(to);
        if (!next) break;
        const basis = r.props.basis === "flow" ? "flow" : "modelled";
        marks.flow.arrows.push({ from: centre(from), to: centre(next), basis });
        if (basis === "flow") marks.flow.flowKnown++;
        else marks.flow.modelledOnly++;
        break;
      }
      case "fills_opening":
      case "penetrates": {
        if (!pierced.has(r.from_full_name)) {
          pierced.add(r.from_full_name);
          marks.penetrations.boxes.push(from);
        }
        const hostName = r.relation === "fills_opening" ? r.props.host_full_name : to;
        const host = typeof hostName === "string" ? boundsOf(hostName) : null;
        if (host) marks.penetrations.links.push({ from: centre(from), to: centre(host) });
        break;
      }
      case "connects_to": {
        const other = boundsOf(to);
        if (other) marks.connections.links.push({ from: centre(from), to: centre(other) });
        break;
      }
    }
  }
  marks.systems.entries = [...systems.values()].sort((a, b) => b.members.length - a.members.length || a.name.localeCompare(b.name));
  return marks;
}

/** How many marks an overlay has: what its toggle says beside it. */
export function markCount(marks: OverlayMarks, id: OverlayId): number {
  switch (id) {
    case "systems":
      return marks.systems.entries.length;
    case "flow":
      return marks.flow.arrows.length;
    case "penetrations":
      return marks.penetrations.boxes.length;
    case "connections":
      return marks.connections.links.length;
  }
}

export function emptyMarks(): OverlayMarks {
  return overlayMarks([], () => null);
}
