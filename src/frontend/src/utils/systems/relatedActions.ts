// What can be done with an element's relations -- add its system, its run, the runs it connects,
// or what it is related to, to the selection; load a system or run -- as plain actions, so every
// surface renders them its own way: core's scene menu, the selected-object panel, and any UI
// shell's menu through the facade (`useRelatedActions`).
//
// SELECTING ADDS. Each "Add ..." puts its elements on top of the current selection, so a selection
// is built a step at a time -- one side's run, the piece between, the other side's run -- and then
// copied, exported or hidden as one.

import type { Related } from "./systems";
import { currentScope, loadMembers, membersOf, runsOf, selectNames, systemKindLabel } from "./systems";

export interface RelatedAction {
  readonly key: string;
  readonly label: string;
  /** Hover text: what the action acts on. */
  readonly title: string;
  readonly disabled?: boolean;
  /** `system`, `run` or `related`: actions of one group belong together. */
  readonly group: "system" | "run" | "related";
  /** Resolves a sentence for a load, nothing for a selection. */
  readonly run: () => Promise<string | void>;
}

/** The actions `related` offers for the element named `name`. */
export function relatedActions(name: string, related: Related): RelatedAction[] {
  const scope = currentScope();
  const { collection, system, run, elements } = related;
  const out: RelatedAction[] = [];
  if (run && system) {
    out.push(
      {
        key: "select-run",
        label: "Add run to selection",
        title: `Every loaded element of ${run}, added to the selection`,
        group: "run",
        run: async () => {
          await selectNames((await membersOf(scope, collection, system.name, run)).map((m) => m.full_name), run, true);
        },
      },
      {
        key: "load-run",
        label: "Load run",
        title: `Load ${run} into the scene`,
        group: "run",
        run: async () => loadMembers(collection, await membersOf(scope, collection, system.name, run), run),
      },
    );
  }
  // An element outside any run that connects to runs -- a penetration piece between the duct
  // above and below a deck: those runs, and the element itself.
  const connected = related.relations.filter((r) => r.relation === "connects_to" && r.to_full_name).map((r) => r.to_full_name!);
  if (!run && connected.length) {
    out.push({
      key: "select-connected-runs",
      label: "Add connected runs to selection",
      title: `${name} and every loaded element of the runs it connects to:\n${connected.join("\n")}`,
      group: "run",
      run: async () => {
        const runs = await runsOf(scope, collection, connected);
        const names = new Set<string>([name, ...connected]);
        for (const r of runs) for (const m of await membersOf(scope, collection, r.system, r.run)) names.add(m.full_name);
        await selectNames(names, `${name} and connected runs`, true);
      },
    });
  }
  if (system) {
    const kind = systemKindLabel(system.kind);
    out.push(
      {
        key: "select-system",
        label: `Add ${kind} system to selection`,
        title: `Every loaded element of ${system.name}, added to the selection`,
        group: "system",
        run: async () => {
          await selectNames((await membersOf(scope, collection, system.name)).map((m) => m.full_name), system.name, true);
        },
      },
      {
        key: "load-system",
        label: `Load ${kind} system`,
        title: `Load ${system.name} into the scene: each element's site, the rest of it hidden`,
        group: "system",
        run: async () => loadMembers(collection, await membersOf(scope, collection, system.name), system.name),
      },
    );
  }
  if (elements.length) {
    out.push({
      key: "select-related",
      label: `Add related to selection (${elements.length})`,
      title: `${name} and what it is related to, added to the selection:\n${elements.join("\n")}`,
      group: "related",
      run: async () => {
        await selectNames([name, ...elements], `${name} and related`, true);
      },
    });
  }
  return out;
}
