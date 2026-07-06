import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useViewerPanelStore} from "@/state/viewerPanelStore";
import {useOptionsStore} from "@/state/optionsStore";

// Shared "View in 3D" dispatcher for the /convert page. Behaviour
// depends on how the page is mounted:
//
// * In-viewer Rnd modal — the 3D scene is already live underneath.
//   Close the modal (+ the Options drawer if open), then hand the
//   derived blob straight to ``overlay_file_in_scene``. No new tab,
//   no scene cold-start, camera state preserved.
//
// * Path-mounted ``/convert`` page — no viewer in this process.
//   Pop the standard ``?scope&file&derived`` deep-link in a new
//   tab and let ``useUrlParamLoad`` pick it up on the viewer side.
//
// Modal detection: ``useViewerPanelStore.open`` is non-null only
// when ``InViewerPanelHost`` is showing. The store doesn't exist
// in the path-mounted route's chunk because that route mounts
// outside ``AdaViewerProvider``, but importing the store module
// is safe (zustand initialises on import; no React state needed).

export async function view_in_3d(
    sourceKey: string,
    derivedKey: string,
    // Load the blob from this scope instead of the currently-selected one.
    // The audit grid passes its run's scope (e.g. corpus:<slug>) so a cell's
    // cached product opens regardless of which scope the user is browsing.
    scopeOverride?: string,
): Promise<void> {
    const inModal = useViewerPanelStore.getState().open !== null;
    const scope = useScopeStore.getState().current;
    const scopePart = scopeOverride ?? (scope ? scopeUrlPart(scope) : "");

    if (!inModal) {
        const params = new URLSearchParams({
            scope: scopePart,
            file: sourceKey,
            derived: derivedKey,
        });
        window.open(`/?${params.toString()}`, "_blank", "noopener");
        return;
    }

    // Modal mode — fold the panels away first so the user sees the
    // viewer immediately, then kick off the load. The drawer would
    // otherwise sit over the canvas until the user dismisses it.
    useViewerPanelStore.getState().closePanel();
    useOptionsStore.getState().setIsOptionsVisible(false);

    const {overlay_file_in_scene} = await import("./overlay_file_in_scene");
    await overlay_file_in_scene(sourceKey, derivedKey, scopeOverride ? {scope: scopeOverride} : undefined);
}
