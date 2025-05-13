// state/modelStore.ts
import * as Comlink from "comlink";
import Worker from "./modelCache.worker.ts?worker";

// This must match what your worker returns via Comlink.expose
export interface ModelData {
  key: string;
  hierarchy: Record<string, [string, string | number]>;
  drawRanges: Record<string, Record<number, [number, number]>>;
}

// A pure-JSON tree you can pass back to your React store
export interface PureTreeNode {
  id: string;
  name: string;
  children: PureTreeNode[];
}

export interface ModelStoreAPI {
  add(
    key: string,
    hierarchy: Record<string, [string, string | number]>,
    drawRanges: Record<string, Record<number, [number, number]>>
  ): Promise<void>;

  get(key: string): Promise<ModelData | undefined>;

  remove(key: string): Promise<void>;

  listKeys(): Promise<string[]>;

  // <— no "?" here! always present
  buildHierarchy(
    hierarchy: Record<string, [string, string | number]>
  ): Promise<PureTreeNode | null>;
}

const worker = new Worker();
export const modelStore = Comlink.wrap<ModelStoreAPI>(worker);
