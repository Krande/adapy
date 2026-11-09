"""Screenshot and DOM snapshot of classic-UI states on one or more servers.

Usage: python capture.py <outdir> <label>=<url>[@<query>] [...] [--only=a,b] [--model=<name>]

`@<query>` appends query parameters for that target (e.g. `layout=engine`).
`--model` names a stored model with a procedural cellbuilder; the cellbuilder
states are skipped without it.

Every target is captured back to back per state, each in a fresh browser
context, so data drift on the shared API affects all sides equally. The canvas
is hidden with CSS rather than a Playwright mask: a mask covers everything
inside the canvas's box, including the panels over it.
"""

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SEARCH_TITLE = "Search all collections (attributes)"
# The build label in Options names the commit, so it differs between any two builds.
HIDE_CANVAS = (
    "canvas { visibility: hidden !important; } "
    "div.text-xs.text-gray-300 > span.font-mono { visibility: hidden !important; }"
)

DOM_JS = r"""
() => {
  const walk = (el) => {
    const node = { tag: el.tagName.toLowerCase() };
    const attrs = {};
    for (const a of [...el.attributes].sort((x, y) => x.name.localeCompare(y.name))) attrs[a.name] = a.value;
    node.attrs = attrs;
    if (node.tag === 'canvas') return node;
    const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).filter(Boolean).join(' ');
    if (text) node.text = text;
    const kids = [...el.children].map(walk);
    if (kids.length) node.children = kids;
    return node;
  };
  return walk(document.body);
}
"""

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
TOP_READY = 'button[aria-label="Toggle scene info"]'


def click(page, sel):
    page.locator(sel).first.click()


def settle(page, ms=1500):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def open_storage_and_model(page, model):
    click(page, 'button[title="Storage"]')
    settle(page, 2500)
    page.get_by_text(model, exact=True).first.click()
    page.locator('button[aria-label="Toggle procedural cellbuilder panel"][aria-pressed="true"]').wait_for(
        timeout=30000
    )


def build_states(model):
    """state -> (action, viewport, query suffix, ready selector)"""
    states = {
        "empty": (None, DESKTOP, "", TOP_READY),
        "options": (lambda p: click(p, 'button[title="Toggle options drawer"]'), DESKTOP, "", TOP_READY),
        "tree": (lambda p: click(p, 'button[aria-label="Show selection tree"]'), DESKTOP, "", TOP_READY),
        "storage": (lambda p: click(p, 'button[title="Storage"]'), DESKTOP, "", TOP_READY),
        "attribute_search": (lambda p: click(p, f'button[aria-label="{SEARCH_TITLE}"]'), DESKTOP, "", TOP_READY),
        "object_info": (lambda p: click(p, 'button[title="Toggle object info"]'), DESKTOP, "", TOP_READY),
        "scene_info": (lambda p: click(p, 'button[aria-label="Toggle scene info"]'), DESKTOP, "", TOP_READY),
        "busy": (
            lambda p: (
                click(p, 'button[title="Toggle options drawer"]'),
                click(p, 'button[title="Toggle object info"]'),
                click(p, 'button[aria-label="Toggle scene info"]'),
            ),
            DESKTOP,
            "",
            TOP_READY,
        ),
        "admin_window": (lambda p: (p.mouse.click(900, 500), p.keyboard.press("Shift+P")), DESKTOP, "", TOP_READY),
        # The Simulation panel as a follower window; it has no canvas or top bar.
        "sim_window": (None, DESKTOP, "&simfollow=probe&panel=none", 'span:text("Simulation")'),
        "mobile_scene": (lambda p: click(p, 'button[aria-label="Toggle scene info"]'), PHONE, "", TOP_READY),
        "mobile_options": (lambda p: click(p, 'button[title="Toggle options drawer"]'), PHONE, "", TOP_READY),
        "mobile_storage": (lambda p: click(p, 'button[title="Storage"]'), PHONE, "", TOP_READY),
        "mobile_admin": (lambda p: (p.mouse.click(200, 600), p.keyboard.press("Shift+P")), PHONE, "", TOP_READY),
        "mobile_attribute_search": (
            lambda p: click(p, f'button[aria-label="{SEARCH_TITLE}"]'),
            PHONE,
            "",
            TOP_READY,
        ),
    }
    if model:
        states["cellbuilder"] = (lambda p: open_storage_and_model(p, model), DESKTOP, "", TOP_READY)
        states["cellbuilder_toggled_off"] = (
            lambda p: (
                open_storage_and_model(p, model),
                click(p, 'button[aria-label="Toggle procedural cellbuilder panel"]'),
            ),
            DESKTOP,
            "",
            TOP_READY,
        )
        states["mobile_cellbuilder"] = (lambda p: open_storage_and_model(p, model), PHONE, "", TOP_READY)
    return states


def parse_target(arg):
    label, rest = arg.split("=", 1)
    url, _, query = rest.partition("@")
    return label, url, query


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("outdir", type=Path)
    parser.add_argument("targets", nargs="+", type=parse_target)
    parser.add_argument("--only", type=lambda s: set(s.split(",")))
    parser.add_argument("--model")
    args = parser.parse_args()

    out = args.outdir
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        for state, (action, vp, suffix, ready) in build_states(args.model).items():
            if args.only and state not in args.only:
                continue
            for label, url, query in args.targets:
                ctx = browser.new_context(viewport=vp, color_scheme="dark", device_scale_factor=1)
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                q = "?ui=core" + (f"&{query}" if query else "") + suffix
                page.goto(url + "/" + q, wait_until="domcontentloaded")
                ok = True
                try:
                    page.locator(ready).first.wait_for(timeout=60000)
                    settle(page, 2500)
                    if action:
                        action(page)
                    settle(page, 2000)
                    # Park the pointer off any control so hover styles are equal.
                    page.mouse.move(vp["width"] - 2, vp["height"] - 2)
                    page.wait_for_timeout(400)
                except Exception as exc:
                    ok = False
                    errors.append(f"action failed: {exc}"[:300])
                page.add_style_tag(content=HIDE_CANVAS)
                page.wait_for_timeout(200)
                page.screenshot(path=str(out / f"{state}__{label}.png"), animations="disabled", caret="hide")
                dom = page.evaluate(DOM_JS)
                (out / f"{state}__{label}.dom.json").write_text(
                    json.dumps(dom, indent=1, sort_keys=True), encoding="utf-8"
                )
                results.setdefault(state, {})[label] = {"ok": ok, "errors": errors}
                ctx.close()
                print(state, label, "ok" if ok else "FAILED", errors[:2], flush=True)
        browser.close()
    (out / "capture.json").write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
