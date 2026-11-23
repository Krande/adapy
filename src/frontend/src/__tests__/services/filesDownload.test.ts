import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

// filesApi.downloadBlob: a presigned attachment URL goes straight to the browser's download
// manager (the body is never fetched into JS); without one, the API-tunnelled blob: path.
// Browser globals are stubbed BEFORE the dynamic import, as providerGrantsApi.test.ts does.
const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};

type Anchor = { href: string; download: string; style: Record<string, string>; click: () => void };
const clicks: { href: string; download: string }[] = [];
const win: Record<string, unknown> = {
  location: { origin: "https://app.example.test", pathname: "/", search: "" },
};
const g = globalThis as unknown as Record<string, unknown>;
g.sessionStorage = storage;
g.localStorage = storage;
g.window = win;
g.document = {
  createElement: (): Anchor => {
    const a: Anchor = {
      href: "",
      download: "",
      style: {},
      click: () => clicks.push({ href: a.href, download: a.download }),
    };
    return a;
  },
  body: { appendChild: () => undefined, removeChild: () => undefined },
};
URL.createObjectURL = () => "blob:local/1";
URL.revokeObjectURL = () => undefined;

const { filesApi } = await import("@/services/api/files");

const PRESIGNED = "https://store.example.test/bkt/users/u/a.glb?X-Amz-Signature=abc";
const DISPOSITION = "attachment; filename=\"Project a.glb\"; filename*=UTF-8''Project%20a.glb";

type Reply = { status: number; body: unknown };
let downloadUrlReply: Reply;
let calls: { url: string; init?: RequestInit }[];
let blobBodyRead: boolean;

g.fetch = async (url: string, init?: RequestInit) => {
  calls.push({ url, init });
  if (url.endsWith("/download-url")) {
    return new Response(JSON.stringify(downloadUrlReply.body), {
      status: downloadUrlReply.status,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (url.includes("/blobs/")) {
    const r = new Response("glTF-bytes", { status: 200 });
    const blob = r.blob.bind(r);
    r.blob = async () => {
      blobBodyRead = true;
      return blob();
    };
    return r;
  }
  return new Response("not found", { status: 404 });
};

beforeEach(() => {
  calls = [];
  clicks.length = 0;
  blobBodyRead = false;
  delete win.ADA_NO_PRESIGNED_URLS;
  downloadUrlReply = {
    status: 200,
    body: { url: PRESIGNED, key: "a.glb", method: "GET", expires_in_seconds: 900, size: 10, content_disposition: DISPOSITION },
  };
});

test("presigned: navigates to the signed URL and never fetches the body", async () => {
  await filesApi.downloadBlob("user:me", "models/a.glb", "Project a.glb");
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/scopes\/user%3Ame\/download-url$/);
  assert.equal(calls.some((c) => c.url.includes("/blobs/")), false);
  assert.equal(blobBodyRead, false);
  assert.deepEqual(clicks, [{ href: PRESIGNED, download: "Project a.glb" }]);
});

test("presigned: the filename and attachment flag ride in the request", async () => {
  await filesApi.downloadBlob("user:me", "models/a.glb", "Project a.glb");
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
    key: "models/a.glb",
    filename: "Project a.glb",
    attachment: true,
  });
});

test("no presigned URLs offered: streams through the API without asking for one", async () => {
  win.ADA_NO_PRESIGNED_URLS = true;
  await filesApi.downloadBlob("user:me", "models/a.glb", "a.glb");
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/blobs\/models%2Fa\.glb$/);
  assert.equal(blobBodyRead, true);
  assert.deepEqual(clicks, [{ href: "blob:local/1", download: "a.glb" }]);
});

for (const status of [503, 500]) {
  test(`/download-url ${status}: falls back to the API path`, async () => {
    downloadUrlReply = { status, body: { detail: "presigned downloads require an S3-compatible backend" } };
    await filesApi.downloadBlob("user:me", "models/a.glb", "renamed.glb");
    assert.equal(calls.length, 2);
    assert.match(calls[1].url, /\/blobs\/models%2Fa\.glb$/);
    assert.deepEqual(clicks, [{ href: "blob:local/1", download: "renamed.glb" }]);
  });
}

test("a server that ignored the attachment request: falls back instead of opening the file", async () => {
  downloadUrlReply = {
    status: 200,
    body: { url: PRESIGNED, key: "a.glb", method: "GET", expires_in_seconds: 900, size: 10 },
  };
  await filesApi.downloadBlob("user:me", "models/a.glb", "a.glb");
  assert.equal(clicks.length, 1);
  assert.equal(clicks[0].href, "blob:local/1");
});

test("a 404 from /download-url is the answer, not a reason to retry through the API", async () => {
  downloadUrlReply = { status: 404, body: { detail: "object not found" } };
  await assert.rejects(filesApi.downloadBlob("user:me", "models/none.glb", "none.glb"));
  assert.equal(calls.length, 1);
  assert.equal(clicks.length, 0);
});

test("programmatic callers get the plain URL request they always sent", async () => {
  await filesApi.requestDownloadUrl("user:me", "models/a.glb");
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { key: "models/a.glb" });
});
