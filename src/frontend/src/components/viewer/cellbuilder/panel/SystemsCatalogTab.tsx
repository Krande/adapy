/**
 * The SYSTEMS tab.
 *
 * Owns: the system-template catalog (inline, on demand) above the service-runs
 * inspector. SystemsTab itself stays mounted (hidden) so its auto-highlight
 * effect keeps tracking a freshly-loaded result.
 */

import React from "react";

import {capabilities} from "@/services/capabilities";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {isReadOnly} from "./chrome";
import {SystemsTab} from "./SystemsTab";

const SystemAdminPanel = React.lazy(
    () => import("@/components/admin/SystemAdminPanel"),
);

export const SystemsCatalogTab: React.FC<{active: boolean}> = ({active}) => {
  const readOnly = isReadOnly(useCellBuilderStore());
  return (
    <>
      {active && !readOnly && capabilities.procedural.supports("syncCatalogEntry") && (
        <React.Suspense fallback={null}>
          <SystemAdminPanel embedded />
        </React.Suspense>
      )}
      <div className="mt-3 pt-2 border-t border-gray-600/50">
        <div className="font-semibold text-gray-300 mb-1.5">
          Service runs
        </div>
        <SystemsTab />
      </div>
    </>
  );
};
