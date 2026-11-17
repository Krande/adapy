import React, {useState, useSyncExternalStore} from "react";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import {ModalFrame} from "@/components/ui/ModalFrame";
import {makePluginContext} from "@/plugins/context";
import {
    getStorageAssetTypes,
    pluginActivationVersion,
    subscribePluginActivation,
    type AdaPluginContext,
    type StorageAssetType,
} from "@/plugins/registry";
import {useViewerStores} from "@/state/AdaViewerContext";
import {usePluginTheme} from "@/state/themeStore";

// The kinds of stored asset plugins add to the storage browser (`storageAssetTypes`, plugin API
// 1.26.0): a section per kind under the file list, filled by the plugin, and the "+" menu's
// entries that open a plugin's upload form in a dialog. Core draws the frame and never learns
// what a kind is.

interface Entry {
    pluginId: string;
    type: StorageAssetType;
}

/** The dialog's title: the plugin's, else the menu entry's text without its ellipsis. */
export function uploadDialogTitle(upload: {label: string; title?: string}): string {
    return upload.title ?? upload.label.replace(/\s*(…|\.\.\.)\s*$/, "");
}

export interface StorageAssetTypesView {
    /** The "+" menu's entries: one per asset type that can be added to. */
    addItems: KebabMenuItem[];
    /** The sections, under the file list; null when no plugin has a kind for this scope. */
    sections: React.ReactNode;
    /** The upload form that is open, as a dialog; null when none is. */
    dialog: React.ReactNode;
}

/** The asset types of the open scope, as the storage browser draws them. */
export function useStorageAssetTypes(scope: string): StorageAssetTypesView {
    const stores = useViewerStores();
    const theme = usePluginTheme();
    // A plugin's `available` may wait on an answer of its own: asked again when it says so.
    useSyncExternalStore(subscribePluginActivation, pluginActivationVersion, pluginActivationVersion);
    const contextFor = (pluginId: string): AdaPluginContext => makePluginContext(pluginId, stores, theme);
    const entries: Entry[] = getStorageAssetTypes(contextFor(""), {scope});
    // The type whose upload form is open, by its id: a type that is no longer there closes it.
    const [uploading, setUploading] = useState<string | null>(null);
    const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
    const open = entries.find((e) => e.type.id === uploading && e.type.upload);

    const addItems: KebabMenuItem[] = entries
        .filter((e) => e.type.upload)
        .map((e) => ({
            key: `asset-type:${e.type.id}`,
            label: e.type.upload!.label,
            onClick: () => setUploading(e.type.id),
        }));

    const sections = entries.length === 0 ? null : (
        <div data-testid="storage-asset-types" className="flex flex-col">
            {entries.map((e) => {
                const isCollapsed = collapsed.has(e.type.id);
                return (
                    <section
                        key={e.type.id}
                        data-asset-type={e.type.id}
                        className="border-t border-gray-700/60 pt-1 mt-1"
                    >
                        <button
                            type="button"
                            className="flex items-center gap-1 px-1 pb-1 w-full text-left text-[10px] uppercase tracking-wide text-gray-400 hover:text-gray-200"
                            aria-expanded={!isCollapsed}
                            onClick={() =>
                                setCollapsed((prev) => {
                                    const next = new Set(prev);
                                    if (!next.delete(e.type.id)) next.add(e.type.id);
                                    return next;
                                })
                            }
                        >
                            <span className="w-3 inline-block">{isCollapsed ? "▸" : "▾"}</span>
                            {e.type.label}
                        </button>
                        {!isCollapsed && (
                            <ErrorBoundary label={`Storage asset type ${e.type.id}`}>
                                {e.type.render(contextFor(e.pluginId), {scope})}
                            </ErrorBoundary>
                        )}
                    </section>
                );
            })}
        </div>
    );

    const close = () => setUploading(null);
    const dialog = open ? (
        <ModalFrame
            title={uploadDialogTitle(open.type.upload!)}
            onClose={close}
            width="max-w-xl"
            testId="storage-asset-upload"
        >
            <ErrorBoundary label={`Storage asset upload ${open.type.id}`}>
                {open.type.upload!.render(contextFor(open.pluginId), {scope, close})}
            </ErrorBoundary>
        </ModalFrame>
    ) : null;

    return {addItems, sections, dialog};
}
