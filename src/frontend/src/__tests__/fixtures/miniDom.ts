// Just enough of a DOM for `react-dom/client` to mount, update and unmount plain
// elements under `node --test`: element and text nodes, attributes, styles,
// child lists, event listeners that do nothing. No layout, no events fired, no
// selectors beyond `querySelector("[attr]")` / `[attr="v"]`. Import it FIRST (before
// `panelRealm`, whose `??=` globals then keep these), then import React dynamically.

class MiniStyle {
  [k: string]: unknown;
  private props = new Map<string, string>();
  setProperty(k: string, v: string): void {
    this.props.set(k, String(v));
  }
  removeProperty(k: string): string {
    const v = this.props.get(k) ?? "";
    this.props.delete(k);
    return v;
  }
  getPropertyValue(k: string): string {
    return this.props.get(k) ?? "";
  }
}

export class MiniNode {
  childNodes: MiniNode[] = [];
  parentNode: MiniNode | null = null;
  ownerDocument: MiniDocument;
  nodeValue: string | null = null;
  onclick: unknown = null;
  constructor(
    public nodeType: number,
    public nodeName: string,
    doc: MiniDocument | null,
  ) {
    this.ownerDocument = doc ?? (this as unknown as MiniDocument);
  }
  get firstChild(): MiniNode | null {
    return this.childNodes[0] ?? null;
  }
  get lastChild(): MiniNode | null {
    return this.childNodes[this.childNodes.length - 1] ?? null;
  }
  get nextSibling(): MiniNode | null {
    const p = this.parentNode;
    if (!p) return null;
    return p.childNodes[p.childNodes.indexOf(this) + 1] ?? null;
  }
  appendChild<T extends MiniNode>(n: T): T {
    n.parentNode?.removeChild(n);
    this.childNodes.push(n);
    n.parentNode = this;
    return n;
  }
  insertBefore<T extends MiniNode>(n: T, ref: MiniNode | null): T {
    if (!ref) return this.appendChild(n);
    n.parentNode?.removeChild(n);
    this.childNodes.splice(this.childNodes.indexOf(ref), 0, n);
    n.parentNode = this;
    return n;
  }
  removeChild<T extends MiniNode>(n: T): T {
    const i = this.childNodes.indexOf(n);
    if (i >= 0) this.childNodes.splice(i, 1);
    n.parentNode = null;
    return n;
  }
  get textContent(): string {
    return this.nodeType === 3 ? (this.nodeValue ?? "") : this.childNodes.map((c) => c.textContent).join("");
  }
  set textContent(v: string) {
    for (const c of [...this.childNodes]) this.removeChild(c);
    if (v) this.appendChild(this.ownerDocument.createTextNode(v));
  }
  addEventListener(): void {}
  removeEventListener(): void {}
  contains(n: MiniNode | null): boolean {
    for (let x = n; x; x = x.parentNode) if (x === this) return true;
    return false;
  }
}

export class MiniElement extends MiniNode {
  attributes = new Map<string, string>();
  style = new MiniStyle();
  namespaceURI = "http://www.w3.org/1999/xhtml";
  constructor(tag: string, doc: MiniDocument) {
    super(1, tag.toUpperCase(), doc);
  }
  get tagName(): string {
    return this.nodeName;
  }
  setAttribute(k: string, v: string): void {
    this.attributes.set(k, String(v));
  }
  getAttribute(k: string): string | null {
    return this.attributes.get(k) ?? null;
  }
  hasAttribute(k: string): boolean {
    return this.attributes.has(k);
  }
  removeAttribute(k: string): void {
    this.attributes.delete(k);
  }
  get id(): string {
    return this.getAttribute("id") ?? "";
  }
  set id(v: string) {
    this.setAttribute("id", v);
  }
  remove(): void {
    this.parentNode?.removeChild(this);
  }
  focus(): void {}
  blur(): void {}
  getBoundingClientRect() {
    return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
  }
  /** Depth-first: the elements below matching `[attr]` or `[attr="value"]`. */
  querySelectorAll(sel: string): MiniElement[] {
    const m = /^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(sel);
    if (!m) throw new Error(`miniDom: unsupported selector ${sel}`);
    const out: MiniElement[] = [];
    const walk = (n: MiniNode) => {
      for (const c of n.childNodes) {
        if (c instanceof MiniElement) {
          const v = c.getAttribute(m[1]);
          if (v !== null && (m[2] === undefined || v === m[2])) out.push(c);
          walk(c);
        }
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel: string): MiniElement | null {
    return this.querySelectorAll(sel)[0] ?? null;
  }
}

export class MiniDocument extends MiniNode {
  documentElement: MiniElement;
  head: MiniElement;
  body: MiniElement;
  activeElement: MiniElement | null = null;
  defaultView: unknown = null;
  constructor() {
    super(9, "#document", null);
    this.documentElement = new MiniElement("html", this);
    this.head = new MiniElement("head", this);
    this.body = new MiniElement("body", this);
    this.appendChild(this.documentElement);
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
  }
  createElement(tag: string): MiniElement {
    return new MiniElement(tag, this);
  }
  createElementNS(_ns: string, tag: string): MiniElement {
    return new MiniElement(tag, this);
  }
  createTextNode(text: string): MiniNode {
    const n = new MiniNode(3, "#text", this);
    n.nodeValue = text;
    return n;
  }
  createComment(text: string): MiniNode {
    const n = new MiniNode(8, "#comment", this);
    n.nodeValue = text;
    return n;
  }
  getElementById(id: string): MiniElement | null {
    return this.documentElement.querySelector(`[id="${id}"]`);
  }
  querySelector(sel: string): MiniElement | null {
    return this.documentElement.querySelector(sel);
  }
  querySelectorAll(sel: string): MiniElement[] {
    return this.documentElement.querySelectorAll(sel);
  }
}

const g = globalThis as Record<string, unknown>;
const cells = new Map<string, string>();
const storage = {
  getItem: (k: string) => (cells.has(k) ? cells.get(k)! : null),
  setItem: (k: string, v: string) => void cells.set(k, String(v)),
  removeItem: (k: string) => void cells.delete(k),
  clear: () => cells.clear(),
  key: () => null,
  get length() {
    return cells.size;
  },
};

export const miniDocument = new MiniDocument();

/** The page's address: a URL whose `replace` / `reload` are recorded, not followed. */
export const navigations: string[] = [];
const location = Object.assign(new URL("http://localhost/"), {
  replace: (url: string) => void navigations.push(url),
  reload: () => void navigations.push("reload"),
  assign: (url: string) => void navigations.push(url),
});

/** Point the page at `href` without navigating (as the address bar would show it). */
export function setLocation(href: string): void {
  location.href = href;
}

const win: Record<string, unknown> = {
  document: miniDocument,
  location,
  history: {
    state: null,
    replaceState: (_s: unknown, _t: string, url: string) => setLocation(new URL(url, location.href).toString()),
    pushState: (_s: unknown, _t: string, url: string) => setLocation(new URL(url, location.href).toString()),
  },
  localStorage: storage,
  sessionStorage: storage,
  devicePixelRatio: 1,
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent: () => true,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  getComputedStyle: () => new MiniStyle(),
  requestAnimationFrame: (cb: (t: number) => void) => setTimeout(() => cb(Date.now()), 0),
  cancelAnimationFrame: (id: ReturnType<typeof setTimeout>) => clearTimeout(id),
  HTMLIFrameElement: class {},
  HTMLElement: MiniElement,
  Element: MiniElement,
  Node: MiniNode,
  innerWidth: 1280,
  innerHeight: 800,
  navigator: { userAgent: "node", platform: "node" },
};
win.window = win;
miniDocument.defaultView = win;

g.window = win;
g.document = miniDocument;
g.localStorage = storage;
g.sessionStorage = storage;
g.HTMLIFrameElement = win.HTMLIFrameElement;
g.HTMLElement = MiniElement;
g.Element = MiniElement;
g.Node = MiniNode;
g.requestAnimationFrame ??= win.requestAnimationFrame;
g.cancelAnimationFrame ??= win.cancelAnimationFrame;
g.IS_REACT_ACT_ENVIRONMENT = true;

/** A fresh container in the body to mount into. */
export function container(): MiniElement {
  return miniDocument.body.appendChild(miniDocument.createElement("div"));
}
