// The local job queue's limits for Settings > Local jobs: load them once, and save. The component
// keeps its input drafts (UI state) and renders what this returns.

import React from "react";

import { localJobsApi, type LocalJobsSettings, type LocalJobsView } from "@/services/api/localJobs";

export interface LocalJobsState {
  /** The limits in force and the queue now; null until the first answer. */
  view: LocalJobsView | null;
  error: string | null;
  isSaving: boolean;
  /** Send new limits. A refusal (a 400 naming the bad value) is shown through `error`. */
  save: (settings: LocalJobsSettings) => Promise<void>;
}

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function useLocalJobs(): LocalJobsState {
  const [view, setView] = React.useState<LocalJobsView | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [isSaving, setIsSaving] = React.useState(false);

  React.useEffect(() => {
    let isMounted = true;
    localJobsApi
      .get()
      .then((v) => {
        if (isMounted) setView(v);
      })
      .catch((e: unknown) => {
        if (isMounted) setError(messageOf(e));
      });
    return () => {
      isMounted = false;
    };
  }, []);

  const save = React.useCallback(async (settings: LocalJobsSettings) => {
    setIsSaving(true);
    setError(null);
    try {
      setView(await localJobsApi.set(settings));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setIsSaving(false);
    }
  }, []);

  return { view, error, isSaving, save };
}
