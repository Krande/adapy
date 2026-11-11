// Draws the core dialog `openCoreDialog` opened (`./coreDialogs`): About, Export, Set colour. Mounted once by a
// composed layout, inside the viewer provider (the export reads the viewer's stores); each
// dialog's code loads the first time it opens.

import React, {Suspense} from "react";

import {closeCoreDialog, useCoreDialog, type CoreDialogRequest} from "./coreDialogs";

const AboutDialog = React.lazy(() => import("@/components/ui/AboutDialog"));
const ExportDialog = React.lazy(() => import("@/components/export/ExportDialog"));
const ObjectColourDialog = React.lazy(() => import("@/components/ui/ObjectColourDialog"));

/** `only`: the kinds this host draws; every kind when left out. A layout without menus has no
 *  About or Export to open, but it has the palette, whose "Set colour of selection…" opens a dialog. */
export const CoreDialogsHost: React.FC<{only?: readonly CoreDialogRequest["kind"][]}> = ({only}) => {
    const open = useCoreDialog();
    if (!open || (only && !only.includes(open.kind))) return null;
    return (
        <Suspense fallback={null}>
            {open.kind === "about" ? (
                <AboutDialog onClose={closeCoreDialog} />
            ) : open.kind === "object-colour" ? (
                <ObjectColourDialog onClose={closeCoreDialog} />
            ) : (
                <ExportDialog key={open.scope} scope={open.scope} onClose={closeCoreDialog} />
            )}
        </Suspense>
    );
};
