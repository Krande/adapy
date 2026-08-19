import assert from "node:assert/strict";
import {test, before} from "node:test";

// Client-render smoke tests for the design-system primitives.
//
// The bar deliberately is not "does it look right" — that is what ?uikit=1 is for.
// It is "does it carry the right semantics": a screen reader must be able to tell a
// pressed toggle from an unpressed one, an icon button must have a name, a tab strip
// must be a tablist. Those are the things that break silently during a refactor and
// that a screenshot review cannot catch.

type JsdomCtor = new (html: string, opts?: Record<string, unknown>) => {window: unknown};
let JSDOM: JsdomCtor | undefined;
try {
    ({JSDOM} = (await import("jsdom" as string)) as {JSDOM: JsdomCtor});
} catch {
    JSDOM = undefined;
}

const skip = JSDOM ? false : "jsdom not installed";

let React: typeof import("react");
let createRoot: typeof import("react-dom/client").createRoot;
let act: typeof import("react").act;
let ui: typeof import("../../components/ui");
let container: HTMLElement;

before(async () => {
    if (!JSDOM) return;
    const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
        url: "http://localhost/",
        pretendToBeVisual: true,
    });
    const w = dom.window as unknown as Window & typeof globalThis;
    const g = globalThis as Record<string, unknown>;
    g.window = w;
    g.document = w.document;
    g.self = w;
    g.navigator ??= w.navigator;
    for (const k of ["HTMLElement", "Element", "Node", "DocumentFragment", "SVGElement", "getComputedStyle"] as const) {
        g[k] = (w as unknown as Record<string, unknown>)[k];
    }
    // React 19 reads this to decide whether act() is legal.
    g.IS_REACT_ACT_ENVIRONMENT = true;

    React = await import("react");
    ({createRoot} = await import("react-dom/client"));
    act = React.act;
    ui = await import("../../components/ui");
    container = w.document.getElementById("root") as HTMLElement;
});

/** Render into a fresh subtree and return it. */
function render(node: unknown): HTMLElement {
    const host = document.createElement("div");
    container.appendChild(host);
    const root = createRoot(host);
    act(() => {
        root.render(node as React.ReactNode);
    });
    return host;
}

test("Button renders every variant and size without throwing", {skip}, () => {
    const variants = ["primary", "secondary", "ghost", "danger", "subtle"] as const;
    const sizes = ["sm", "md", "lg"] as const;
    for (const variant of variants) {
        for (const size of sizes) {
            const host = render(
                React.createElement(ui.Button, {variant, size}, `${variant}/${size}`),
            );
            const btn = host.querySelector("button");
            assert.ok(btn, `${variant}/${size} produced no button`);
            // Defaults to type=button: a Button inside a form must not submit it by
            // accident, which is the single most common bug this replaces.
            assert.equal(btn!.getAttribute("type"), "button");
        }
    }
});

test("Button loading disables the control and marks it busy", {skip}, () => {
    const host = render(React.createElement(ui.Button, {loading: true}, "Saving"));
    const btn = host.querySelector("button")!;
    assert.equal(btn.disabled, true, "a loading button must not be clickable");
    assert.equal(btn.getAttribute("aria-busy"), "true");
});

test("IconButton exposes its tooltip as the accessible name", {skip}, () => {
    const host = render(
        React.createElement(ui.IconButton, {
            tooltip: "Zoom to fit",
            icon: React.createElement(ui.Icon, {name: "expand"}),
        }),
    );
    const btn = host.querySelector("button")!;
    assert.equal(btn.getAttribute("aria-label"), "Zoom to fit");
    assert.equal(btn.getAttribute("title"), "Zoom to fit");
    // The icon must actually render — this regressed once: `icon` was destructured
    // but never placed in the children, so every icon button was an empty box.
    assert.ok(btn.querySelector("svg"), "IconButton rendered no glyph");
});

test("IconButton and ToggleButton report press state", {skip}, () => {
    const a = render(
        React.createElement(ui.IconButton, {tooltip: "Snap", pressed: true, icon: React.createElement(ui.Icon, {name: "move"})}),
    );
    assert.equal(a.querySelector("button")!.getAttribute("aria-pressed"), "true");

    const b = render(React.createElement(ui.ToggleButton, {pressed: false}, "Snap"));
    assert.equal(b.querySelector("button")!.getAttribute("aria-pressed"), "false");
});

test("Tabs is a tablist with exactly one selected tab and roving tabindex", {skip}, () => {
    const host = render(
        React.createElement(ui.Tabs, {
            label: "Scene sections",
            value: "b",
            onChange: () => {},
            items: [
                {id: "a", label: "A"},
                {id: "b", label: "B"},
                {id: "c", label: "C", disabled: true},
            ],
        }),
    );
    const list = host.querySelector('[role="tablist"]')!;
    assert.equal(list.getAttribute("aria-label"), "Scene sections");

    const tabs = [...host.querySelectorAll('[role="tab"]')];
    assert.equal(tabs.length, 3);
    assert.equal(tabs.filter((t) => t.getAttribute("aria-selected") === "true").length, 1);
    // Only the active tab is in the page tab order (WAI-ARIA tabs pattern).
    assert.equal(tabs.filter((t) => t.getAttribute("tabindex") === "0").length, 1);
});

test("SegmentedControl is a radiogroup, not a tablist", {skip}, () => {
    // The distinction matters: tabs switch a region of content, this sets a value.
    const host = render(
        React.createElement(ui.SegmentedControl, {
            label: "Display mode",
            value: "solid",
            onChange: () => {},
            options: [
                {value: "solid", label: "Solid"},
                {value: "wire", label: "Wire"},
            ],
        }),
    );
    assert.ok(host.querySelector('[role="radiogroup"]'));
    const radios = [...host.querySelectorAll('[role="radio"]')];
    assert.equal(radios.length, 2);
    assert.equal(radios.filter((r) => r.getAttribute("aria-checked") === "true").length, 1);
});

test("Field associates its label, hint and error with the control", {skip}, () => {
    const host = render(
        // children passed in props: Field declares `children` as required, and
        // createElement's rest-arg form does not satisfy that overload.
        React.createElement(ui.Field, {
            label: "Section name",
            hint: "Shown in the outliner",
            children: React.createElement(ui.Input, {}),
        }),
    );
    const input = host.querySelector("input")!;
    const label = host.querySelector("label")!;
    assert.ok(input.id, "Field must give the control an id");
    assert.equal(label.getAttribute("for"), input.id, "label must point at the control");
    assert.equal(input.getAttribute("aria-describedby"), `${input.id}-hint`);

    const bad = render(
        React.createElement(ui.Field, {
            label: "Scale",
            error: "Must be positive",
            children: React.createElement(ui.Input, {}),
        }),
    );
    const badInput = bad.querySelector("input")!;
    assert.equal(badInput.getAttribute("aria-invalid"), "true");
    assert.ok(bad.querySelector('[role="alert"]'), "an error must be announced");
});

test("Checkbox sets the indeterminate DOM property", {skip}, () => {
    // indeterminate has no HTML attribute — it can only be set imperatively, which is
    // exactly the sort of thing that gets dropped in a rewrite.
    const host = render(React.createElement(ui.Checkbox, {label: "All", indeterminate: true, readOnly: true, checked: false}));
    const box = host.querySelector("input")!;
    assert.equal(box.indeterminate, true);
});

test("Switch carries role=switch", {skip}, () => {
    const host = render(React.createElement(ui.Switch, {label: "On-demand render", defaultChecked: true}));
    assert.equal(host.querySelector("input")!.getAttribute("role"), "switch");
});

test("Splitter is a separator with a full value range", {skip}, () => {
    const host = render(
        React.createElement(ui.Splitter, {
            orientation: "vertical",
            label: "Resize left dock",
            value: 220,
            onChange: () => {},
            min: 120,
            max: 420,
        }),
    );
    const sep = host.querySelector('[role="separator"]')!;
    assert.equal(sep.getAttribute("aria-label"), "Resize left dock");
    assert.equal(sep.getAttribute("aria-orientation"), "vertical");
    assert.equal(sep.getAttribute("aria-valuenow"), "220");
    assert.equal(sep.getAttribute("aria-valuemin"), "120");
    assert.equal(sep.getAttribute("aria-valuemax"), "420");
    // Keyboard-operable: the layout must be usable without a pointer.
    assert.equal(sep.getAttribute("tabindex"), "0");
});

test("StatusDot always carries a text label", {skip}, () => {
    // Colour alone is not an accessible status, and is unreadable for anyone with a
    // colour-vision deficiency.
    const host = render(React.createElement(ui.StatusDot, {tone: "fail", label: "Conversion failed"}));
    assert.match(host.textContent ?? "", /Conversion failed/);
});

test("Toolbar exposes role and orientation", {skip}, () => {
    const host = render(
        React.createElement(ui.Toolbar, {label: "Transform tools", orientation: "vertical"}, "x"),
    );
    const bar = host.querySelector('[role="toolbar"]')!;
    assert.equal(bar.getAttribute("aria-label"), "Transform tools");
    assert.equal(bar.getAttribute("aria-orientation"), "vertical");
});

test("every registered icon renders a glyph", {skip}, () => {
    // The registry is the shell's indirection layer — a PanelDef names its icon as a
    // string. A name that maps to nothing must fail here, not show a blank toolbar.
    for (const name of ui.ICON_NAMES) {
        const host = render(React.createElement(ui.Icon, {name}));
        assert.ok(host.querySelector("svg"), `icon "${name}" rendered no svg`);
    }
});

test("icons set no colour of their own", {skip}, () => {
    // The C4D principle: chrome stays low-chroma so the 3D content carries the colour.
    // An icon must take the colour of whatever it sits in.
    for (const name of ui.ICON_NAMES) {
        const host = render(React.createElement(ui.Icon, {name}));
        for (const el of host.querySelectorAll("[stroke],[fill]")) {
            for (const attr of ["stroke", "fill"] as const) {
                const v = el.getAttribute(attr);
                if (v == null) continue;
                assert.ok(
                    v === "currentColor" || v === "none",
                    `icon "${name}" hardcodes ${attr}="${v}" — use currentColor`,
                );
            }
        }
    }
});

test("every icon has an intrinsic size for direct (non-<Icon>) use", {skip}, () => {
    // Regression. M1 stripped width/height from the icon roots so the <Icon> wrapper
    // could own sizing — which silently broke every call site that renders an icon
    // component directly. The classic Menu.tsx does exactly that, and six of its eight
    // toolbar icons collapsed to 0x0.
    //
    // <Icon> still wins: it sizes via `[&>svg]:w-full`, and CSS beats presentation
    // attributes. So the attribute is a floor, not a constraint.
    for (const name of ui.ICON_NAMES) {
        const Glyph = ui.ICONS[name];
        const host = render(React.createElement(Glyph));
        const svg = host.querySelector("svg")!;
        assert.ok(
            svg.getAttribute("width") && svg.getAttribute("height"),
            `icon "${name}" has no intrinsic size — it will render 0x0 when used directly`,
        );
    }
});
