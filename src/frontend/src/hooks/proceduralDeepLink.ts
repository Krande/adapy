import {viewerApi} from "@/services/viewerApi";
import {useCellBuilderStore} from "@/state/cellBuilderStore";

// ``?procedural=<model id>``: open a procedural model in the cell builder, the way a
// row in the Storage browser opens it.
//
// The ``?file=`` deep link's counterpart for a model that is a DOCUMENT rather than a
// file: a tool that wrote one (a local viewer's launcher puts a workbook's model there)
// hands the page its id, and together with ``?mode=build`` the page opens on it. Read by
// useUrlParamLoad, which also handles ``?scope=`` and strips the query string.

/** The URL parameter that opens a procedural model in the cell builder. */
export const PROCEDURAL_PARAM = "procedural";

/** Fetch the model and open it; resolves whether it opened. */
export async function openProceduralFromUrl(scopeKey: string, modelId: string): Promise<boolean> {
    try {
        const detail = await viewerApi.getProceduralModel(scopeKey, modelId);
        useCellBuilderStore.getState().open(detail.id, detail.name, detail.revision, detail.doc);
        return true;
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[useUrlParamLoad] could not open procedural model", modelId, err);
        return false;
    }
}
