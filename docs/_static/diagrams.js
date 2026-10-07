/* Interactive Mermaid diagrams for the docs.
 *
 * ```mermaid fences are emitted as <div class="ada-diagram"> (zensical.toml custom fence) and
 * rendered here instead of by the theme, whose renderer draws into a closed shadow root that
 * nothing can be attached to. On top of the plain diagram this adds:
 *
 *   - an "Enlarge" button (and click-on-diagram) opening a full-screen view with wheel/pinch
 *     zoom, drag to pan, and zoom/fit controls;
 *   - connection tracing: hovering a box (tap on touch screens) dims everything but that box,
 *     its edges and its direct neighbours, in flowcharts and class diagrams;
 *   - `click NODE href "url" "tooltip"` lines in the source become links with tooltips.
 *
 * Diagrams re-render when the light/dark palette is switched.
 */
(function () {
  "use strict";

  const MERMAID = "https://unpkg.com/mermaid@11/dist/mermaid.esm.min.mjs";
  // ELK lays out nested subgraphs far better than the default dagre: the architecture
  // diagrams are mostly grouped flowcharts. If it fails to load, dagre is used.
  const ELK = "https://unpkg.com/@mermaid-js/layout-elk@0/dist/mermaid-layout-elk.esm.min.mjs";
  let mermaidPromise = null;
  let elkReady = false;
  let seq = 0;

  function loadMermaid() {
    if (!mermaidPromise) {
      mermaidPromise = import(MERMAID).then(async (m) => {
        const mermaid = m.default;
        try {
          const elk = await import(ELK);
          mermaid.registerLayoutLoaders(elk.default);
          elkReady = true;
        } catch (err) {
          console.warn("ELK layout unavailable, falling back to dagre", err);
        }
        return mermaid;
      });
    }
    return mermaidPromise;
  }

  function isDark() {
    return document.body.getAttribute("data-md-color-scheme") === "slate";
  }

  async function renderInto(target, source) {
    const mermaid = await loadMermaid();
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
      theme: isDark() ? "dark" : "default",
      layout: elkReady ? "elk" : "dagre",
      elk: { mergeEdges: false, nodePlacementStrategy: "BRANDES_KOEPF" },
      fontFamily: getComputedStyle(document.body).fontFamily,
      flowchart: { htmlLabels: true, useMaxWidth: true },
      sequence: { useMaxWidth: true },
    });
    const id = `ada-diagram-${++seq}`;
    // bindFunctions is deliberately not called: the diagrams only use `click … href`, which
    // Mermaid emits as plain SVG <a> links, and its floating tooltip <div> cannot show above the
    // full-screen <dialog>. Tooltips become native SVG <title>s instead (nativeTooltips).
    const { svg } = await mermaid.render(id, source);
    target.innerHTML = svg;
    const el = target.querySelector("svg");
    resolveSiteLinks(el);
    nativeTooltips(el);
    wireTracing(el);
    return el;
  }

  /* `click` links to other pages are written relative to the site root
   * ("architecture/fea/#viewer-bake"), the same on every page however deep it is. The theme
   * puts the page's own path to the root in #__config ("base": "../.."); prefix it here. */
  function siteBase() {
    try {
      return JSON.parse(document.getElementById("__config").textContent).base || ".";
    } catch (err) {
      return ".";
    }
  }

  function resolveSiteLinks(svg) {
    if (!svg) return;
    const base = siteBase().replace(/\/$/, "");
    svg.querySelectorAll("a").forEach((a) => {
      for (const attr of ["href", "xlink:href"]) {
        const url = a.getAttribute(attr);
        if (url && !/^([a-z][a-z0-9+.-]*:|#|\/)/i.test(url)) a.setAttribute(attr, `${base}/${url}`);
      }
    });
  }

  function nativeTooltips(svg) {
    if (!svg) return;
    svg.querySelectorAll("[title]").forEach((el) => {
      const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
      t.textContent = el.getAttribute("title");
      el.removeAttribute("title");
      el.insertBefore(t, el.firstChild);
    });
  }

  /* ── connection tracing ─────────────────────────────────────────────── */

  const NODE_ID = /-(?:flowchart|classId)-(.+)-\d+$/;
  const EDGE_ID = /^(?:L|id)_(.+)_\d+$/;

  function wireTracing(svg) {
    if (!svg) return;
    const nodes = new Map(); // id -> g.node
    svg.querySelectorAll("g.node").forEach((g) => {
      const m = NODE_ID.exec(g.id);
      if (m) nodes.set(m[1], g);
    });
    if (!nodes.size) return; // sequence diagrams etc: enlarge only

    // An edge id is L_<src>_<dst>_<n>; ids may contain "_", so split where both halves are nodes.
    const edges = new Map(); // data-id -> {src, dst, els: []}
    svg.querySelectorAll("[data-id]").forEach((el) => {
      const key = el.getAttribute("data-id");
      const m = EDGE_ID.exec(key);
      if (!m) return;
      let entry = edges.get(key);
      if (!entry) {
        const parts = m[1].split("_");
        for (let i = 1; i < parts.length && !entry; i++) {
          const a = parts.slice(0, i).join("_");
          const b = parts.slice(i).join("_");
          if (nodes.has(a) && nodes.has(b)) entry = { src: a, dst: b, els: [] };
        }
        if (!entry) return;
        edges.set(key, entry);
      }
      // The label's <g class="label"> sits inside <g class="edgeLabel">; dim the whole label.
      entry.els.push(el.closest(".edgeLabel") || el);
    });

    const clear = () => {
      svg.classList.remove("ada-tracing");
      svg.querySelectorAll(".ada-hl").forEach((e) => e.classList.remove("ada-hl"));
    };
    const trace = (id) => {
      clear();
      svg.classList.add("ada-tracing");
      nodes.get(id).classList.add("ada-hl");
      edges.forEach((e) => {
        if (e.src !== id && e.dst !== id) return;
        e.els.forEach((el) => el.classList.add("ada-hl"));
        nodes.get(e.src).classList.add("ada-hl");
        nodes.get(e.dst).classList.add("ada-hl");
      });
    };

    let pinned = null;
    nodes.forEach((g, id) => {
      g.addEventListener("mouseenter", () => pinned || trace(id));
      g.addEventListener("mouseleave", () => pinned || clear());
      g.addEventListener("click", (ev) => {
        if (g.closest("a")) return; // a linked box navigates
        ev.stopPropagation();
        pinned = pinned === id ? null : id;
        pinned ? trace(id) : clear();
      });
    });
    svg.addEventListener("click", (ev) => {
      if (pinned) {
        // First click on empty space releases the pinned box; it does not also enlarge.
        ev.stopPropagation();
        pinned = null;
        clear();
      }
    });
  }

  /* ── enlarge view ───────────────────────────────────────────────────── */

  let dialog = null;

  function ensureDialog() {
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.className = "ada-diagram-dialog";
    dialog.innerHTML = `
      <div class="ada-diagram-dialog__bar">
        <span class="ada-diagram-dialog__hint">Scroll to zoom · drag to pan · hover a box to trace it · click to pin</span>
        <button type="button" data-act="in" title="Zoom in" aria-label="Zoom in">+</button>
        <button type="button" data-act="out" title="Zoom out" aria-label="Zoom out">−</button>
        <button type="button" data-act="fit" title="Fit to screen" aria-label="Fit to screen">Fit</button>
        <button type="button" data-act="close" title="Close (Esc)" aria-label="Close">✕</button>
      </div>
      <div class="ada-diagram-dialog__viewport"><div class="ada-diagram-dialog__canvas"></div></div>`;
    document.body.appendChild(dialog);
    dialog.addEventListener("click", (ev) => {
      if (ev.target === dialog) dialog.close();
    });
    return dialog;
  }

  function panZoom(viewport, canvas, svg) {
    const vb = svg.viewBox.baseVal;
    const w = vb && vb.width ? vb.width : svg.getBBox().width;
    const h = vb && vb.height ? vb.height : svg.getBBox().height;
    svg.removeAttribute("style");
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    let s = 1, x = 0, y = 0;
    const apply = () => (canvas.style.transform = `translate(${x}px, ${y}px) scale(${s})`);
    const fit = () => {
      const r = viewport.getBoundingClientRect();
      s = Math.min(r.width / w, r.height / h) * 0.95;
      x = (r.width - w * s) / 2;
      y = (r.height - h * s) / 2;
      apply();
    };
    const zoomAt = (factor, cx, cy) => {
      const ns = Math.min(Math.max(s * factor, 0.1), 12);
      x = cx - ((cx - x) * ns) / s;
      y = cy - ((cy - y) * ns) / s;
      s = ns;
      apply();
    };
    viewport.onwheel = (ev) => {
      ev.preventDefault();
      const r = viewport.getBoundingClientRect();
      zoomAt(Math.exp(-ev.deltaY * 0.0015), ev.clientX - r.left, ev.clientY - r.top);
    };
    // Pointer drag pans; two pointers pinch-zoom.
    const pts = new Map();
    let last = null, pinch = null;
    viewport.onpointerdown = (ev) => {
      pts.set(ev.pointerId, ev);
      last = { x: ev.clientX, y: ev.clientY };
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        pinch = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      }
    };
    viewport.onpointermove = (ev) => {
      if (!pts.has(ev.pointerId)) return;
      pts.set(ev.pointerId, ev);
      if (pts.size === 2 && pinch) {
        const [a, b] = [...pts.values()];
        const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        const r = viewport.getBoundingClientRect();
        zoomAt(d / pinch, (a.clientX + b.clientX) / 2 - r.left, (a.clientY + b.clientY) / 2 - r.top);
        pinch = d;
      } else if (last) {
        if (!viewport.hasPointerCapture(ev.pointerId)) {
          if (Math.hypot(ev.clientX - last.x, ev.clientY - last.y) < 4) return; // still a click
          viewport.setPointerCapture(ev.pointerId);
        }
        x += ev.clientX - last.x;
        y += ev.clientY - last.y;
        last = { x: ev.clientX, y: ev.clientY };
        apply();
      }
    };
    const end = (ev) => {
      pts.delete(ev.pointerId);
      if (pts.size < 2) pinch = null;
      if (!pts.size) last = null;
    };
    viewport.onpointerup = end;
    viewport.onpointercancel = end;
    fit();
    return {
      fit,
      zoom: (f) => {
        const r = viewport.getBoundingClientRect();
        zoomAt(f, r.width / 2, r.height / 2);
      },
    };
  }

  async function enlarge(source) {
    const dlg = ensureDialog();
    const viewport = dlg.querySelector(".ada-diagram-dialog__viewport");
    const canvas = dlg.querySelector(".ada-diagram-dialog__canvas");
    canvas.innerHTML = "";
    canvas.style.transform = "";
    dlg.showModal();
    const svg = await renderInto(canvas, source);
    const ctl = panZoom(viewport, canvas, svg);
    dlg.querySelector(".ada-diagram-dialog__bar").onclick = (ev) => {
      const act = ev.target.closest("button")?.dataset.act;
      if (act === "in") ctl.zoom(1.25);
      else if (act === "out") ctl.zoom(0.8);
      else if (act === "fit") ctl.fit();
      else if (act === "close") dlg.close();
    };
  }

  /* ── page wiring ────────────────────────────────────────────────────── */

  function setUp(block) {
    if (block.dataset.adaSource) return;
    const source = block.textContent;
    block.dataset.adaSource = source;
    block.textContent = "";

    const figure = document.createElement("div");
    figure.className = "ada-diagram__figure";
    figure.title = "Click to enlarge";
    const bar = document.createElement("div");
    bar.className = "ada-diagram__bar";
    const linked = /^\s*click\s+\S+\s+href\b/m.test(source);
    bar.innerHTML = `<span class="ada-diagram__hint">Hover a box to trace its connections${
      linked ? " · underlined boxes open the page or code" : ""
    }</span>
      <button type="button" class="ada-diagram__enlarge" title="Enlarge">⤢ Enlarge</button>`;
    block.append(bar, figure);

    bar.querySelector("button").addEventListener("click", () => enlarge(source));
    figure.addEventListener("click", (ev) => {
      // Boxes trace/pin and links navigate; a click on empty diagram space enlarges.
      if (ev.target.closest("g.node, a")) return;
      enlarge(source);
    });
    return renderInto(figure, source).catch((err) => {
      figure.innerHTML = `<pre class="ada-diagram__error">${String(err.message || err)}</pre>`;
    });
  }

  function renderAll() {
    document.querySelectorAll("div.ada-diagram").forEach((b) => setUp(b));
  }

  function rerenderAll() {
    document.querySelectorAll("div.ada-diagram[data-ada-source]").forEach((b) => {
      const figure = b.querySelector(".ada-diagram__figure");
      if (figure) renderInto(figure, b.dataset.adaSource).catch(() => {});
    });
  }

  function start() {
    renderAll();
    new MutationObserver(rerenderAll).observe(document.body, {
      attributes: true,
      attributeFilter: ["data-md-color-scheme"],
    });
  }

  // Material-style themes expose document$ for instant navigation; fall back to DOMContentLoaded.
  if (typeof window.document$ !== "undefined" && window.document$.subscribe) {
    let observed = false;
    window.document$.subscribe(() => {
      if (!observed) {
        observed = true;
        start();
      } else renderAll();
    });
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
