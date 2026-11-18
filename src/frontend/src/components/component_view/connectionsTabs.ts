// The Connections panel's own tabs (`core:component-controls`) and which of them are offered. Kept
// apart from the component so the rules can be tested.
//
// Core's tabs come first, in this order, then the plugins' tabs (region `connection-builder`):
//
//   Builder   the connection builder; where the builder is offered (`ada dev connections`, or a
//             scope that offers specs).
//   Clashes   the clash check that identifies joints in the loaded source; always. It needs no
//             content of its own to be worth showing: it runs against whatever source is loaded
//             and says plainly when that source has no members to check.
//   Overlays  what the joint tabs draw in 3D (the joint being developed, the clash run's markers
//             and contacts); always.
//   Review    the joints found or detailed; contextual, while there are any (`hasJoints`).
//
// The last three were the Scene panel's Joints group ("Clashes", "Joint overlays", "Joints",
// shown in its strip as "Clashes", "Overlays", "Review"); its "Connection" tab was this builder.

import {
  BUILDER_TAB,
  CLASHES_TAB,
  JOINT_OVERLAYS_TAB,
  JOINTS_REVIEW_TAB,
  type ConnectionsCoreTab,
} from "@/state/connectionBuilderTabStore";

export interface ConnectionsTabDef {
  id: ConnectionsCoreTab;
  label: string;
  /** Shown only while its content exists (the strip marks it). */
  ctx?: boolean;
}

export const CONNECTIONS_CORE_TABS: readonly ConnectionsTabDef[] = [
  { id: BUILDER_TAB, label: "Builder" },
  { id: CLASHES_TAB, label: "Clashes" },
  { id: JOINT_OVERLAYS_TAB, label: "Overlays" },
  { id: JOINTS_REVIEW_TAB, label: "Review", ctx: true },
];

/** What the offered core tabs depend on. */
export interface ConnectionsTabFacts {
  /** The builder is offered here (`connectionBuilderOffered`). */
  builder: boolean;
  /** There are joints to review: produced in the loaded model's take-off, generated in this
   *  session, or identified by a clash run. */
  joints: boolean;
}

/** Core's tabs offered, in order. Never empty: Clashes and Overlays are always offered. */
export function connectionsCoreTabs(facts: ConnectionsTabFacts): ConnectionsTabDef[] {
  return CONNECTIONS_CORE_TABS.filter(
    (t) => (t.id !== BUILDER_TAB || facts.builder) && (t.id !== JOINTS_REVIEW_TAB || facts.joints),
  );
}

/** The tab on show: the picked one while it is offered, else the first offered. */
export function connectionsActiveTab(picked: string, offered: readonly string[]): string | undefined {
  return offered.includes(picked) ? picked : offered[0];
}
