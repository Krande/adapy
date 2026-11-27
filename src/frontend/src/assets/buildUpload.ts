// The body of `POST /assets/build/result`: a build the browser made, framed for the server.
//
// Mirrors `encode_build_upload` in `ada/assets/build_upload.py` (the server's reader, and the
// rules it checks the upload against): a 4-byte little-endian header length, the header as
// UTF-8 JSON (`{request, summary, files: [{name, size, content_encoding?}]}`), then each file's
// bytes, in order, back to back. One request per build, no multipart parser on either side.
//
// Pure (no fetch, no DOM): the API call and the browser-build runner both build on it.

/** One file a builder wrote, by its plain name under the derived prefix core composed. */
export interface BuildUploadFile {
  readonly name: string;
  readonly data: Uint8Array;
  readonly contentEncoding?: string | null;
}

/** The node the build is for, as `POST /assets/build` was asked (revision as it answered). */
export interface BuildUploadRequest {
  readonly provider: string;
  readonly collection: string;
  readonly node: string;
  readonly subject?: string;
  readonly revision: string;
}

export function encodeBuildUpload(
  request: BuildUploadRequest,
  summary: Record<string, unknown>,
  files: readonly BuildUploadFile[],
): Uint8Array {
  const header = new TextEncoder().encode(
    JSON.stringify({
      request,
      summary,
      files: files.map((f) => ({
        name: f.name,
        size: f.data.byteLength,
        ...(f.contentEncoding ? { content_encoding: f.contentEncoding } : {}),
      })),
    }),
  );
  const total = 4 + header.byteLength + files.reduce((n, f) => n + f.data.byteLength, 0);
  const out = new Uint8Array(total);
  new DataView(out.buffer).setUint32(0, header.byteLength, true);
  out.set(header, 4);
  let offset = 4 + header.byteLength;
  for (const f of files) {
    out.set(f.data, offset);
    offset += f.data.byteLength;
  }
  return out;
}

/** The files a builder wrote, named under `derivedPrefix`; a key outside it is refused here,
 *  before anything is sent (the server refuses it too). */
export function filesUnderPrefix(
  derivedPrefix: string,
  written: readonly { key: string; bytes: Uint8Array; contentEncoding?: string | null }[],
): BuildUploadFile[] {
  const prefix = `${derivedPrefix.replace(/\/+$/, "")}/`;
  return written.map((w) => {
    const name = w.key.startsWith(prefix) ? w.key.slice(prefix.length) : "";
    if (!name || name.includes("/")) {
      throw new Error(`the build wrote ${JSON.stringify(w.key)}, outside its derived prefix ${prefix}`);
    }
    return { name, data: w.bytes, contentEncoding: w.contentEncoding ?? null };
  });
}
