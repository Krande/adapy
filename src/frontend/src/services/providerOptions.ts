// Where provider options live: one blob per collection, `assets/_options/<collection>.json`
// (`@/assets/providerOptions`), in the PROVIDER STORE -- once, whichever scope a request comes from.
// An admin edits them there; a member's scope never holds its own.

import { PROVIDER_STORE_SCOPE } from "@/assets/providerStore";
import { ApiError } from "@/services/api/client";
import { filesApi } from "@/services/api/files";
import {
  EMPTY_PROVIDER_OPTIONS,
  parseProviderOptionsDoc,
  providerOptionsKey,
  serialiseProviderOptionsDoc,
  withListedChoices,
  withProviderValues,
  type OptionChoice,
  type ProviderOptionValues,
  type ProviderOptionsDoc,
} from "@/assets/providerOptions";

/** The stored document. Nothing stored yet is nothing set; any OTHER failure throws -- a request
 *  must not go out without values that could not be read, and a save must not overwrite them. */
export async function readProviderOptions(collection: string): Promise<ProviderOptionsDoc> {
  let bytes: ArrayBuffer;
  try {
    bytes = await filesApi.getBlob(PROVIDER_STORE_SCOPE, providerOptionsKey(collection));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return EMPTY_PROVIDER_OPTIONS;
    throw e;
  }
  return parseProviderOptionsDoc(new TextDecoder().decode(bytes));
}

/** Remember what one provider's choices job answered, in the LATEST stored document. */
export async function saveListedChoices(
  collection: string,
  providerId: string,
  options: Readonly<Record<string, readonly OptionChoice[]>>,
): Promise<ProviderOptionsDoc> {
  const next = withListedChoices(await readProviderOptions(collection), providerId, options, new Date().toISOString());
  await filesApi.putBlob(
    PROVIDER_STORE_SCOPE,
    providerOptionsKey(collection),
    new Blob([serialiseProviderOptionsDoc(next)], { type: "application/json" }),
  );
  return next;
}

/** Replace one provider's values in the LATEST stored document; returns what was written. */
export async function saveProviderOptions(
  collection: string,
  providerId: string,
  values: ProviderOptionValues,
): Promise<ProviderOptionsDoc> {
  const next = withProviderValues(await readProviderOptions(collection), providerId, values, new Date().toISOString());
  await filesApi.putBlob(
    PROVIDER_STORE_SCOPE,
    providerOptionsKey(collection),
    new Blob([serialiseProviderOptionsDoc(next)], { type: "application/json" }),
  );
  return next;
}
