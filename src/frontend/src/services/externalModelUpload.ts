// The pure half of the external-model upload path: what to send, and how.
//
// Split from `externalModels.ts` for the same reason `externalModelsBinding.ts`
// is — that module imports `viewerApi`, which touches `sessionStorage` at load
// and so cannot be imported under `node --test`. These are decisions about
// bytes and headers with no network in them, and they are exactly the part
// worth asserting.

/** Whether a provider accepts models. Reported by the listing actions.
 *
 * A provider opts in by implementing the upload seam; nothing is configured.
 * So a read-only catalogue simply never offers the button, rather than offering
 * one that fails when pressed. */
export function canUpload(listing: { can_upload?: boolean }): boolean {
  return listing.can_upload === true;
}

/** Bytes that are already a gzip member.
 *
 * The magic is `1f 8b`, and checking it is what stops a second compression
 * pass. Re-gzipping an already-gzipped export produces a file the viewer
 * cannot read while every header still claims it is correct — the exact mirror
 * of storing gzip without saying so. */
export function isGzipped(head: Uint8Array): boolean {
  return head.length >= 2 && head[0] === 0x1f && head[1] === 0x8b;
}

/** The body to PUT, and the headers to PUT it with.
 *
 * The provider says how it wants the model stored; this decides only whether
 * the bytes in hand already satisfy that. Three cases, and the third is the one
 * worth stating:
 *
 *   * asked for gzip, already gzipped  -> send as-is, keep the header
 *   * asked for gzip, not gzipped      -> compress, keep the header
 *   * asked for gzip, cannot compress  -> send raw and DROP the header
 *
 * Dropping it matters. A browser without `CompressionStream` that kept the
 * header would upload plain bytes labelled gzip, which is the same corruption
 * from the other side. Uncompressed and honest beats compressed and wrong. */
export async function prepareUploadBody(
  file: Blob,
  headers: Record<string, string>,
): Promise<{ body: Blob; headers: Record<string, string> }> {
  const wantsGzip = Object.entries(headers).some(
    ([k, v]) => k.toLowerCase() === "content-encoding" && v.toLowerCase() === "gzip",
  );
  if (!wantsGzip) return { body: file, headers };

  const head = new Uint8Array(await file.slice(0, 2).arrayBuffer());
  if (isGzipped(head)) return { body: file, headers };

  const CS = (globalThis as { CompressionStream?: typeof CompressionStream }).CompressionStream;
  if (typeof CS !== "function") {
    const withoutEncoding = Object.fromEntries(
      Object.entries(headers).filter(([k]) => k.toLowerCase() !== "content-encoding"),
    );
    return { body: file, headers: withoutEncoding };
  }
  const gz = await new Response(file.stream().pipeThrough(new CS("gzip"))).blob();
  return { body: gz, headers };
}
