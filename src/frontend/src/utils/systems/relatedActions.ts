// What can be done with an element's relations -- select or load its system or run, select what it
// is related to -- as plain actions, so every surface renders them its own way: core's scene menu,
// the selected-object panel, and any UI shell's menu through the facade (`useRelatedActions`).

import type { Related } from "./systems";
import { currentScope, loadMembers, membersOf, selectNames, systemKindLabel } from "./systems";

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
  if (system) {
    const kind = systemKindLabel(system.kind);
    out.push(
      {
        key: "select-system",
        label: `Select ${kind} system`,
        title: `Every loaded element of ${system.name}`,
        group: "system",
        run: async () => {
          await selectNames((await membersOf(scope, collection, system.name)).map((m) => m.full_name), system.name);
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
  if (run && system) {
    out.push(
      {
        key: "select-run",
        label: "Select run",
        title: `Every loaded element of ${run}`,
        group: "run",
        run: async () => {
          await selectNames((await membersOf(scope, collection, system.name, run)).map((m) => m.full_name), run);
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
  if (elements.length) {
    out.push({
      key: "select-related",
      label: `Select related (${elements.length})`,
      title: `${name} and what it is related to:\n${elements.join("\n")}`,
      group: "related",
      run: async () => {
        await selectNames([name, ...elements], `${name} and related`);
      },
    });
  }
  return out;
}
