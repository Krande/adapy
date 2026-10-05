// Where provider options live: one blob per collection, `assets/_options/<collection>.json`
// (`@/assets/providerOptions`). In the scope, shared by everyone in it.

import { ApiError, type ScopeUrl } from "@/services/api/client";
import { filesApi } from "@/services/api/files";
import {
  EMPTY_PROVIDER_OPTIONS,
  parseProviderOptionsDoc,
  providerOptionsKey,
  serialiseProviderOptionsDoc,
  withProviderValues,
  type ProviderOptionValues,
  type ProviderOptionsDoc,
} from "@/assets/providerOptions";

/** The stored document. Nothing stored yet is nothing set; any OTHER failure throws -- a request
 *  must not go out without values that could not be read, and a save must not overwrite them. */
export async function readProviderOptions(scope: string, collection: string): Promise<ProviderOptionsDoc> {
  let bytes: ArrayBuffer;
  try {
    bytes = await filesApi.getBlob(scope as ScopeUrl, providerOptionsKey(collection));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return EMPTY_PROVIDER_OPTIONS;
    throw e;
  }
  return parseProviderOptionsDoc(new TextDecoder().decode(bytes));
}

/** Replace one provider's values in the LATEST stored document; returns what was written. */
export async function saveProviderOptions(
  scope: string,
  collection: string,
  providerId: string,
  values: ProviderOptionValues,
): Promise<ProviderOptionsDoc> {
  const next = withProviderValues(await readProviderOptions(scope, collection), providerId, values, new Date().toISOString());
  await filesApi.putBlob(
    scope as ScopeUrl,
    providerOptionsKey(collection),
    new Blob([serialiseProviderOptionsDoc(next)], { type: "application/json" }),
  );
  return next;
}
