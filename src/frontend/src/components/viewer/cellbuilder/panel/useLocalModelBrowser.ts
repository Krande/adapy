/**
 * The LOCAL-DISK model browser.
 *
 * Owns: listing the procedural models saved to local disk and opening one
 * through the store's public open()/loadFromDoc() path. Only the websocket
 * transport ever has anything to list, so `supported` is false over REST and the
 * header's button renders nothing there. The fetch-then-decide logic itself lives
 * in utils/cellbuilder/localModelBrowser so it stays testable against a fake
 * capability; this hook only wires it to the real one.
 */

import React from "react";

import {LOCAL_MODEL_SCOPE, capabilities, type ProceduralModelEntry} from "@/services/capabilities";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {openLocalModel as openLocalModelDecision} from "@/utils/cellbuilder/localModelBrowser";

export interface LocalModelBrowser {
  supported: boolean;
  entries: ProceduralModelEntry[];
  busy: boolean;
  error: string | null;
  menuOpen: boolean;
  setMenuOpen: React.Dispatch<React.SetStateAction<boolean>>;
  refresh: () => void;
  open: (entry: ProceduralModelEntry) => void;
}

export function useLocalModelBrowser(): LocalModelBrowser {
  const [localModelsMenuOpen, setLocalModelsMenuOpen] = React.useState(false);
  const [localModels, setLocalModels] = React.useState<ProceduralModelEntry[]>([]);
  const [localModelsBusy, setLocalModelsBusy] = React.useState(false);
  const [localModelsError, setLocalModelsError] = React.useState<string | null>(null);
  const listModelsSupported = capabilities.procedural.supports("listModels");

  const refreshLocalModels = React.useCallback(() => {
    if (!listModelsSupported) return;
    setLocalModelsBusy(true);
    setLocalModelsError(null);
    capabilities.procedural
      .listModels(LOCAL_MODEL_SCOPE)
      .then((entries) => {
        // Most recently saved first -- the model someone is most likely reopening.
        setLocalModels([...entries].sort((a, b) => b.modifiedAt - a.modifiedAt));
      })
      .catch((e) => setLocalModelsError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLocalModelsBusy(false));
  }, [listModelsSupported]);

  // List once up front so the browser has something to show the first time it's opened, without
  // requiring an explicit click first.
  React.useEffect(() => {
    refreshLocalModels();
  }, [refreshLocalModels]);

  // Open a local model through the store's existing public open()/loadFromDoc() path -- the
  // fetch-then-decide logic itself lives in `openLocalModel` (utils/cellbuilder/localModelBrowser)
  // so it is testable against a fake capability; this panel only wires it to the real one and to
  // `useCellBuilderStore`'s public actions, never its internals.
  const handleOpenLocalModel = React.useCallback((entry: ProceduralModelEntry) => {
    void openLocalModelDecision(entry, {
      fetchModel: (source) => capabilities.procedural.fetchModel(source),
      canEdit: capabilities.procedural.canEdit,
      open: useCellBuilderStore.getState().open,
      loadFromDoc: useCellBuilderStore.getState().loadFromDoc,
    }).then((result) => {
      if (result.ok) {
        setLocalModelsMenuOpen(false);
        setLocalModelsError(null);
      } else {
        setLocalModelsError(result.error);
      }
    });
  }, []);

  return {
    supported: listModelsSupported,
    entries: localModels,
    busy: localModelsBusy,
    error: localModelsError,
    menuOpen: localModelsMenuOpen,
    setMenuOpen: setLocalModelsMenuOpen,
    refresh: refreshLocalModels,
    open: handleOpenLocalModel,
  };
}
