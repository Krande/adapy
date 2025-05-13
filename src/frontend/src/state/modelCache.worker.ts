// state/modelCache.worker.ts
import Dexie, { Table } from "dexie";
import * as Comlink from "comlink";

export interface ModelData {
  key: string;
  hierarchy: Record<string, [string, string | number]>;
  drawRanges: Record<string, Record<number, [number, number]>>;
}

// the shape of the pure tree we’ll return
export interface PureTreeNode {
  id: string;
  name: string;
  children: PureTreeNode[];
}

class ModelWorkerAPI {
  private db: Dexie;
  private models!: Table<ModelData, string>;

  constructor() {
    this.db = new Dexie("ModelCacheDB");
    this.db.version(1).stores({ models: "&key" });
    this.models = this.db.table("models") as Table<ModelData, string>;
  }

  async add(key: string, hierarchy: Record<string, [string, string | number]>, drawRanges: Record<string, Record<number, [number, number]>>): Promise<void> {
    await this.models.put({ key, hierarchy, drawRanges });
  }

  async get(key: string): Promise<ModelData | undefined> {
    return this.models.get(key);
  }

  async remove(key: string): Promise<void> {
    await this.models.delete(key);
  }

  async listKeys(): Promise<string[]> {
    return this.models.toCollection().primaryKeys();
  }

  /**
   * Build a pure parent/child tree from your id_hierarchy JSON.
   * Does NOT touch any THREE.Object3D or meshRefs.
   */
  buildHierarchy(
    hierarchy: Record<string, [string, string | number]>
  ): PureTreeNode | null {
    // instantiate nodes
    const nodes: Record<string, PureTreeNode> = {};
    for (const [id, [name]] of Object.entries(hierarchy)) {
      nodes[id] = { id, name, children: [] };
    }

    // link parents → children
    let root: PureTreeNode | null = null;
    for (const [id, [, parent]] of Object.entries(hierarchy)) {
      if (parent === "*" || parent === null) {
        root = nodes[id];
      } else {
        const p = nodes[String(parent)];
        if (p) p.children.push(nodes[id]);
      }
    }

    // natural‐sort each children array
    const sortRec = (n: PureTreeNode) => {
      n.children.sort((a, b) =>
        a.name.localeCompare(b.name, undefined, {
          numeric: true,
          sensitivity: "base",
        })
      );
      n.children.forEach(sortRec);
    };
    if (root) sortRec(root);

    return root;
  }
}

Comlink.expose(new ModelWorkerAPI());
