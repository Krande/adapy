# The adapy viewer frontend

A React + three.js application for looking at, post-processing, authoring and moving
engineering models. It started as a websocket GLB viewer; it is now four workspaces over
one 3D scene, and the name "viewer" has stayed for the same reason "Photoshop" did.

## Getting it running

```bash
npm install
npm run dev              # http://localhost:5173
npm run dev -- --host    # reachable from another device on the LAN
npm run dev:rest         # same, but pretending to be the hosted deployment
```

A bare dev server has no server and no model, so two fixtures ship with the repo:

| URL | What you get |
|---|---|
| `?demo=1` | a small structural model — enough to exercise selection, the tree and properties |
| `?build=1` | a procedural model open in the cellbuilder |
| `?uikit=1` | the design-system gallery (dev builds only) |

`npm run dev:rest` adds a stub REST backend (scopes, a file list, blob reads, and enough
of the procedural API to create and open a model), which is the only way to see
Storage, Convert, upload, or admin. It deliberately does not implement
everything — unimplemented routes return a JSON 404 that names the route, so "the fixture
does not do that" is distinguishable from "the feature is broken".

## Layout of the code

```
src/
  shell/        the application frame: menus, modes, docks, command palette, layout state
  components/   panels and the design system (components/ui)
  state/        zustand stores — business logic, not presentation
  utils/        three.js controllers, picking, cellbuilder, storage, scene handlers
  services/     network: websocket, REST, conversion engines
  plugins/      the extension-point registry
```

The important split is **`shell/` and `components/ui/` own presentation; `state/`,
`utils/` and `services/` own behaviour.** A change that makes the UI prettier should not
appear in the second group. The rebuild that produced the current shell held that line
deliberately — see `docs/UI_BASELINE.md`, which records what was decided and why, and is
worth reading before making structural changes.

## The frame

**Menus** — `File · Edit · View · Tools · Window · Help`, generated from the command
registry rather than hand-listed. Add a command in `shell/commands.ts` and name its id in
`shell/menuModel.ts`; a typo fails a test rather than leaving a gap someone has to find.

**Modes** — `Convert · Build · Inspect · Results`. Ordered as work flows. Files is **not** a mode: it is a flyout column toggled from the top of the rail, available in every mode. A mode changes
which *panels* are offered and which tools sit in the strip under the switcher; it never
changes selection, camera, visibility or what is loaded, and it never activates itself.
That contract is written at the top of `shell/modeStore.ts` and enforced by
`modeSemantics.test.ts`.

**Panels** — one entry each in `shell/panelRegistry.ts`. Registering a panel is all it
takes to get a menu item, a command-palette entry and a dock home.

**Docks** — hand-rolled, not a docking library. `ThreeCanvas` appends its WebGL canvas
imperatively, and every docking library re-parents DOM nodes when you drag a tab, which
would orphan the canvas. Docks and panels also choose between tabbed and stacked
arrangements from their measured height (`shell/dockArrangement.ts`,
`shell/tabArrangement.ts`).

## Three builds, one codebase

| Command | Target | Constraint |
|---|---|---|
| `npm run build` | desktop / pip (`.show()`) | single chunk, inlined into one HTML file — bundle size matters |
| `npm run build:serve` | the hosted viewer | chunk-split |
| `npm run build:embed` | `mountViewer()` for Jupyter and docs | one ESM file, CSS wrapped in `@scope` |

**All three must pass before any UI change lands.** The embed one breaks in ways the
others cannot: its CSS is `@scope`-wrapped, so a `:root` rule inside it matches nothing,
and `@layer`ed styles lose to a host page's unlayered ones regardless of specificity. See
`vite.plugin-embed-css.mjs`.

## Testing

```bash
npm test          # node --test, ~400 tests
npx tsc --noEmit  # three pre-existing errors, unrelated to the UI
```

The runner takes an **explicit file list** in `package.json`, so a new test file does
nothing until it is added there. This has silently swallowed new tests more than once.

Anything that reaches a zustand store reaches the model worker (`?worker&inline`), which
only a bundler resolves — so logic worth testing is separated from its wiring
(`commandFilter`, `gizmoRules`, `classifyFiles`, `menuModel`, the two arrangement rules).
If a test cannot import your module, that is usually the reason, and splitting the pure
part out is the fix rather than a workaround.

`noAdHocChrome.test.ts` fails on any new file using a raw Tailwind palette colour. Use
the semantic utilities (`bg-surface-*`, `text-content-*`, `bg-accent`, `text-fail`) or a
primitive from `components/ui`. The allowlist is a burn-down of files not yet converted
and only ever shrinks.

## Routes

Every route is the same shell under a different profile (`shell/profiles.ts`), which is
what decides whether it gets a canvas, docks, menus and a way back.

| Route | Profile | Notes |
|---|---|---|
| `/` | `viewer` | the application |
| `/convert`, `/admin` | `page` | canvas-less, reduced bar, "Back to the viewer" |
| `?simfollow=` | `window` | canvas-less pop-out; deliberately no way "back" |
| `?uikit=1` | — | the design-system gallery, dev builds only |
| `/auth/callback` | — | outside the shell; it is a redirect handler |

`page` and `window` mount outside `AdaViewerProvider` with `canvas: false`, so no 3D
scene, websocket or tree spins up and three.js stays out of their entry chunk. That
separation is the reason these were once standalone pages; folding them into the shell
kept it.

## Known rough edges

- The template picker ("New model from template") is still only in Storage's "+" menu,
  unlike "New procedural model…" which is also in File and the Build toolbar.
- Escape is inconsistent in the cellbuilder: it cancels a modal move but accepts a widget
  drag. Both are defensible on their own; together they are not.
