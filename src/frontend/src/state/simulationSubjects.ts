// What a simulation run can be started ON, as the viewer holds it: the subjects something has
// OFFERED.
//
// A subject is a file in a scope of a kind core defines (`ada.simulation.subjects` on the server:
// `weld-assembly`, ...). Whatever produces one -- the connection studio, a detailing plugin -- stores
// the file and offers it here; the Simulations panel lists the offers and starts a run on the one
// chosen, with the providers that accept its kind. Producer and provider never know each other:
// this store and the server's `accepts` are all they share.
//
// Offers live in memory, for the session: a subject is a stored file, so one that is no longer
// offered is still there and can be offered again. Newest first.

import { create } from "zustand";

import type { ScopeUrl } from "@/services/api/client";

/** A subject on offer. `scope` is where its file is (a run on it is started in that scope). */
export interface SimulationSubject {
  /** One of the server's subject kinds (`SUBJECT_KINDS`), e.g. `"weld-assembly"`. */
  readonly kind: string;
  /** Storage key of the file in `scope`. */
  readonly key: string;
  readonly scope: ScopeUrl;
  /** What a person calls it: shown in the picker and on the run that was started on it. */
  readonly label: string;
  /** Who offered it, for a person ("Connection studio"); a plugin's id when it did not say. */
  readonly source?: string;
}

/** What identifies an offer: the same file in the same scope is one subject. */
export function subjectId(subject: Pick<SimulationSubject, "scope" | "key">): string {
  return `${subject.scope}\u0000${subject.key}`;
}

export interface SimulationSubjectState {
  /** The subjects on offer, newest first. */
  offered: readonly SimulationSubject[];
  /** Offer a subject (or offer it again: it becomes the newest and its label is renewed). */
  offer: (subject: SimulationSubject) => void;
  /** Take back the offer of the subject with this key (in `scope` when given, else in any). */
  withdraw: (key: string, scope?: ScopeUrl) => void;
}

export const useSimulationSubjectStore = create<SimulationSubjectState>((set) => ({
  offered: [],
  offer: (subject) =>
    set((s) => ({ offered: [subject, ...s.offered.filter((o) => subjectId(o) !== subjectId(subject))] })),
  withdraw: (key, scope) =>
    set((s) => ({ offered: s.offered.filter((o) => !(o.key === key && (scope === undefined || o.scope === scope))) })),
}));

/** The picker's "run on nothing" choice: a run that needs no subject while some are on offer. */
export const NO_SUBJECT = "";

/** The subject the picker shows: the one named by `chosenId` while it is still on offer, else the
 *  newest offer (`null` as the id: nothing chosen yet); null when nothing is offered or the choice
 *  is `NO_SUBJECT`. */
export function chosenSubject(
  offered: readonly SimulationSubject[],
  chosenId: string | null,
): SimulationSubject | null {
  if (chosenId === NO_SUBJECT) return null;
  return offered.find((o) => subjectId(o) === chosenId) ?? offered[0] ?? null;
}

/** Whether a provider (as the server lists it) takes this kind of subject. A server that lists no
 *  `accepts` (an older one) takes none. */
export function providerAccepts(provider: { readonly accepts?: readonly string[] }, kind: string): boolean {
  return (provider.accepts ?? []).includes(kind);
}

/** The subject a provider's parameter form is told about: the chosen one when the provider takes its
 *  kind, else none -- a run it cannot take is not started on it, so the form must not act as if. */
export function formSubject(
  provider: { readonly accepts?: readonly string[] },
  subject: Pick<SimulationSubject, "kind" | "key" | "label"> | null,
): { kind: string; key: string; label: string } | null {
  if (!subject || !providerAccepts(provider, subject.kind)) return null;
  return { kind: subject.kind, key: subject.key, label: subject.label };
}

/** What the panel says against a provider that cannot take the chosen subject. */
export function doesNotTake(kind: string): string {
  return `does not take a ${kind}`;
}

/** The providers that accept the subject's kind first, the others after, each group in its order.
 *  Without a subject the order is untouched. */
export function acceptingFirst<P extends { readonly accepts?: readonly string[] }>(
  providers: readonly P[],
  subject: Pick<SimulationSubject, "kind"> | null,
): P[] {
  if (!subject) return [...providers];
  return [
    ...providers.filter((p) => providerAccepts(p, subject.kind)),
    ...providers.filter((p) => !providerAccepts(p, subject.kind)),
  ];
}
