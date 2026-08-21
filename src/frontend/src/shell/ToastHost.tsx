import React, {Suspense} from "react";
import {ErrorBoundary} from "@/components/common/ErrorBoundary";
import {runtime} from "@/runtime/config";
import {Z} from "./zIndex";

// Ambient, transient notifications — conversion progress, upload state, audit sweeps.
//
// Each of these previously positioned itself: ConversionProgress fixed bottom-right,
// UploadContextMenu's toast fixed bottom-left, each with its own z-index guess. That is
// how two toasts end up overlapping, and how one ends up behind a panel.
//
// One host, one corner, one layer from the registry. The individual components keep
// their own visibility logic (they render null when idle), so nothing about when a toast
// appears changes — only where it lands and what it stacks against.
//
// Z.toast sits ABOVE Z.contextMenu and below Z.dialog: a job failing while a menu is
// open must still be readable, but a modal the user is actively answering wins.

const ConversionProgress = React.lazy(() => import("@/components/conversion/ConversionProgress"));
const UploadContextMenu = React.lazy(() => import("@/components/upload/UploadContextMenu"));

export default function ToastHost() {
    // REST-only: conversion jobs and uploads do not exist in the desktop/WS build, and
    // lazy-importing them there would pull the REST chunk into a bundle that never needs
    // it.
    if (!runtime.isRestMode()) return null;

    return (
        <div
            style={{zIndex: Z.toast}}
            className="pointer-events-none fixed inset-0 flex flex-col justify-end items-end gap-2 p-3"
        >
            {/* pointer-events-auto per child: the host itself must not swallow clicks
                aimed at the viewport behind it. */}
            <div className="pointer-events-auto flex flex-col items-end gap-2">
                <ErrorBoundary label="Conversion progress">
                    <Suspense fallback={null}>
                        <ConversionProgress />
                    </Suspense>
                </ErrorBoundary>
                <ErrorBoundary label="Upload">
                    <Suspense fallback={null}>
                        <UploadContextMenu />
                    </Suspense>
                </ErrorBoundary>
            </div>
        </div>
    );
}
