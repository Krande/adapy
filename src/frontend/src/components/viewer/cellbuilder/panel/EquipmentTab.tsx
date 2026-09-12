/**
 * The EQUIPMENT tab.
 *
 * Owns: the per-scope equipment catalog, inline (browse / select / manage),
 * mounted only while the tab is active so its WebGL preview and fetches spin up
 * on demand. The catalog admin panel edits a PER-SCOPE DB catalog, which
 * read-only mode has none of, so it is omitted there entirely — rendering it
 * would show empty CRUD tables over a store that cannot be written.
 */

import React from "react";

import {capabilities} from "@/services/capabilities";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {isReadOnly} from "./chrome";

const EquipmentAdminPanel = React.lazy(
    () => import("@/components/admin/EquipmentAdminPanel"),
);

export const EquipmentTab: React.FC<{active: boolean}> = ({active}) => {
  const readOnly = isReadOnly(useCellBuilderStore());
  return (
    <>
      {active &&
        (readOnly || !capabilities.procedural.supports("syncCatalogEntry") ? (
          <p className="italic text-gray-500">
            The equipment catalog is served by the model store, which this
            model was not loaded from.
          </p>
        ) : (
          <React.Suspense
            fallback={<p className="text-gray-500">Loading catalog…</p>}
          >
            <EquipmentAdminPanel embedded />
          </React.Suspense>
        ))}
    </>
  );
};
