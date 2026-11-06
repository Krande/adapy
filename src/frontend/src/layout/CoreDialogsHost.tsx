// Draws the core dialog `openCoreDialog` opened (`./coreDialogs`): About, Export. Mounted once by a
// composed layout, inside the viewer provider (the export reads the viewer's stores); each
// dialog's code loads the first time it opens.

import React, {Suspense} from "react";

import {closeCoreDialog, useCoreDialog} from "./coreDialogs";

const AboutDialog = React.lazy(() => import("@/components/ui/AboutDialog"));
const ExportDialog = React.lazy(() => import("@/components/export/ExportDialog"));

export const CoreDialogsHost: React.FC = () => {
    const open = useCoreDialog();
    if (!open) return null;
    return (
        <Suspense fallback={null}>
            {open.kind === "about" ? (
                <AboutDialog onClose={closeCoreDialog} />
            ) : (
                <ExportDialog key={open.scope} scope={open.scope} onClose={closeCoreDialog} />
            )}
        </Suspense>
    );
};
