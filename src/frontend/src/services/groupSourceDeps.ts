// The live reads behind a saved-group member's source reference (`@/utils/groups/groupSourceRefs`):
// a subject-revision's manifest through the blob route, the revision a node resolves to now through
// its delivery claim, element ids from the collection's attribute index, and which models were
// opened from disk.
//
// The element ids are read from the attribute index only -- never by walking the Sources tree, which
// may switch that tab to another collection: saving a group must not move the panel under the user.
// A member the index does not place is saved with its names alone (`element_id: null`).

import { assetKey } from "@/assets/keys";
import { assetsApi } from "@/services/api/assets";
import { attributeSearchApi } from "@/services/api/attributeSearch";
import type { ScopeUrl } from "@/services/api/client";
import { filesApi } from "@/services/api/files";
import { groupsAreShared } from "@/services/savedGroups";
import { MANIFEST_FILENAME, type SourceRefDeps } from "@/utils/groups/groupSourceRefs";
import { rowsFromIndex } from "@/utils/groups/groupSources";
import { isLocalSource } from "@/utils/groups/localSources";
import type { GroupMember } from "@/utils/groups/savedGroups";

async function textOf(bytes: ArrayBuffer): Promise<string> {
  const head = new Uint8Array(bytes, 0, Math.min(2, bytes.byteLength));
  // A JSON blob may be gzip-at-rest.
  if (head[0] === 0x1f && head[1] === 0x8b && typeof DecompressionStream !== "undefined") {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return await new Response(stream).text();
  }
  return new TextDecoder().decode(bytes);
}

/** The live deps for `scope`, or null without a server (nothing to refer to there). */
export function realSourceRefDeps(scope: string): SourceRefDeps | null {
  if (!groupsAreShared()) return null;
  const s = scope as ScopeUrl;
  return {
    scope,
    async readManifest(collection, subject, revision) {
      const bytes = await filesApi.getBlob(s, assetKey(collection, subject, revision, MANIFEST_FILENAME));
      return JSON.parse(await textOf(bytes));
    },
    async currentRevision(t) {
      const claim = await assetsApi.getAssetDelivery(s, t.provider, t.collection, t.subject);
      return claim.revision || null;
    },
    async elementIds(members: readonly GroupMember[]) {
      const out = new Map<string, string>();
      const byCollection = new Map<string, GroupMember[]>();
      for (const m of members) {
        if (m.target.kind !== "node" || m.element === null) continue;
        byCollection.set(m.target.collection, [...(byCollection.get(m.target.collection) ?? []), m]);
      }
      await Promise.all(
        [...byCollection].map(async ([collection, list]) => {
          try {
            const names = [...new Set(list.map((m) => m.element as string))];
            const answer = await attributeSearchApi.collectionNodes(s, collection, { fullNames: names });
            if (!answer.indexed) return;
            for (const [key, row] of rowsFromIndex(list, answer.nodes)) out.set(key, row);
          } catch {
            // No index to ask: the members keep their names only.
          }
        }),
      );
      return out;
    },
    isLocalFile: (sourceKey) => isLocalSource(sourceKey),
  };
}
