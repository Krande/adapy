// The WebGPU embed's toolbar: a row of toggle buttons at the figure's top left, each opening a panel
// below it (object info, scene info, simulation controls), styled as Ada Studio's top bar. Plain DOM:
// the embed has no React. Clicks and drags on the bar and its panels stay out of the camera.

export interface ToolbarPanel {
    title: string;
    /** Inline SVG (24x24 viewBox, currentColor). */
    icon: string;
    /** Fill the panel's body; called on opening and on `refresh`. */
    render: (body: HTMLElement) => void;
    open?: boolean;
}

const ICON_ATTR = 'width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
export const ICONS = {
    info: `<svg ${ICON_ATTR}><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>`,
    scene: `<svg ${ICON_ATTR}><path d="M12 3 3 8l9 5 9-5-9-5Z"/><path d="m3 13 9 5 9-5"/></svg>`,
    sim: `<svg ${ICON_ATTR}><path d="M3 12h3l3-7 6 14 3-7h3"/></svg>`,
};

const BTN = "display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border:0;" +
    "border-radius:4px;color:#fff;cursor:pointer;padding:0;";
const BTN_OFF = "background:#1d4ed8;";
const BTN_ON = "background:#1e3a8a;box-shadow:inset 0 2px 4px rgba(0,0,0,.35);";

export class Toolbar {
    private readonly root: HTMLDivElement;
    private readonly row: HTMLDivElement;
    private readonly panels: HTMLDivElement;
    private readonly entries = new Map<string, {panel: ToolbarPanel; btn: HTMLButtonElement; box: HTMLDivElement | null}>();

    constructor(host: HTMLElement) {
        this.root = document.createElement("div");
        this.root.style.cssText =
            "position:absolute;left:6px;top:6px;right:6px;bottom:44px;display:flex;flex-direction:column;" +
            "align-items:flex-start;gap:6px;pointer-events:none;z-index:2;";
        this.row = document.createElement("div");
        this.row.style.cssText = "display:flex;gap:6px;pointer-events:auto;";
        this.panels = document.createElement("div");
        this.panels.style.cssText =
            "display:flex;flex-direction:column;gap:6px;align-items:flex-start;min-height:0;max-width:min(320px,70%);" +
            "overflow:auto;pointer-events:auto;";
        this.root.append(this.row, this.panels);
        for (const el of [this.row, this.panels]) {
            // the camera listens on the host: nothing on the bar or in a panel orbits it
            for (const t of ["pointerdown", "wheel", "dblclick", "contextmenu"]) el.addEventListener(t, (e) => e.stopPropagation());
        }
        host.appendChild(this.root);
    }

    add(id: string, panel: ToolbarPanel): void {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.title = panel.title;
        btn.setAttribute("aria-label", panel.title);
        btn.innerHTML = panel.icon;
        btn.addEventListener("click", () => this.toggle(id));
        this.row.appendChild(btn);
        this.entries.set(id, {panel, btn, box: null});
        this.sync(id);
    }

    toggle(id: string, open?: boolean): void {
        const e = this.entries.get(id);
        if (!e) return;
        e.panel.open = open ?? !e.panel.open;
        this.sync(id);
    }

    isOpen(id: string): boolean {
        return !!this.entries.get(id)?.panel.open;
    }

    /** Re-render an open panel (its content changed). */
    refresh(id: string): void {
        const e = this.entries.get(id);
        if (e?.box) {
            const body = e.box.lastElementChild as HTMLElement;
            body.innerHTML = "";
            e.panel.render(body);
        }
    }

    private sync(id: string): void {
        const e = this.entries.get(id)!;
        const open = !!e.panel.open;
        e.btn.style.cssText = BTN + (open ? BTN_ON : BTN_OFF);
        e.btn.setAttribute("aria-pressed", String(open));
        if (open && !e.box) {
            e.box = document.createElement("div");
            e.box.style.cssText =
                "background:rgba(255,255,255,.96);color:#1e293b;border:1px solid #cbd5e1;border-radius:6px;" +
                "font:11px/1.4 system-ui;padding:6px 8px;box-shadow:0 1px 3px rgba(0,0,0,.15);max-width:100%;";
            const head = document.createElement("div");
            head.textContent = e.panel.title;
            head.style.cssText = "font-weight:600;margin-bottom:3px;";
            const body = document.createElement("div");
            e.box.append(head, body);
            // panels keep the order of their buttons
            const order = [...this.entries.keys()];
            const after = order.slice(order.indexOf(id) + 1).map((k) => this.entries.get(k)!.box).find(Boolean);
            this.panels.insertBefore(e.box, after ?? null);
            e.panel.render(body);
        } else if (!open && e.box) {
            e.box.remove();
            e.box = null;
        }
    }
}

/** A two-column table of label / value rows. */
export function infoTable(rows: [string, string][]): HTMLTableElement {
    const t = document.createElement("table");
    t.style.cssText = "border-collapse:collapse;";
    for (const [k, v] of rows) {
        const tr = t.insertRow();
        const a = tr.insertCell(), b = tr.insertCell();
        a.textContent = k;
        b.textContent = v;
        a.style.cssText = "color:#64748b;padding:1px 8px 1px 0;vertical-align:top;white-space:nowrap;";
        b.style.cssText = "padding:1px 0;word-break:break-word;";
    }
    return t;
}

export function hint(text: string): HTMLDivElement {
    const d = document.createElement("div");
    d.textContent = text;
    d.style.cssText = "color:#64748b;";
    return d;
}

export function fmtNum(v: number): string {
    if (!Number.isFinite(v)) return String(v);
    if (v === 0) return "0";
    const a = Math.abs(v);
    return a >= 1e4 || a < 1e-3 ? v.toExponential(3) : String(Number(v.toPrecision(5)));
}
