
## Builder View tab dissolved; Mesh tab scoped to Inspect/Results

The Builder panel's **View** tab is gone, split three ways by asking what each control
actually was:

- **View state** (representation topology/simulation/detail, superimpose, side-by-side,
  port overlay, recentre) was never panel content — it is seven commands. They now live
  in `buildActions.ts` + `commands.ts` and surface under **View ▸ Builder**. Menus have
  no pressed styling, so `ACTIONS` gained `checked`/`checkedTitle`: the title renders as
  `✓ Topology` when the state is on. Without that a menu of toggles gives no feedback at
  all about which one is active.
- **The two compile-output toggles** (`buildSim`, `buildDetail`) moved into BuildTab's
  Compile settings. They were mis-filed: they control what the compiler *emits*, not
  what you *look at*.
- `ViewTab.tsx` was deleted and de-registered.

The Scene panel's **Mesh** tab is now mode-scoped. `TAB_META` gained an optional `modes`
field and `tabsForMode` applies it alongside the existing contextual gate. Mesh quality
asks whether a discretisation is good enough to trust — Inspect and Results work. In
Build you are authoring the geometry the mesh will later be made *from*, so there is
nothing to assess yet.

**The `?worker&inline` barrier again, fifth time.** The new `sceneTabs.test.ts` imported
the rule from `SceneBody`, which reaches a store, which reaches the model worker, which
only a bundler resolves. Same `does not provide an export named 'default'` as every
previous occurrence. Fix is always the same: extract the pure rule to `src/shell/`
(`sceneTabs.ts`), have the component re-export it. Assume any rule worth testing must
start life outside a component.

## Files header aligned with the dock panels

The Files header's buttons were hand-rolled at `min-h-[40px] min-w-[40px]` with 24px
icons, sitting one column away from Model and Outliner whose headers use `IconButton
size="sm"` at 22px. The mismatch was visible side by side. They now use the same
`IconButton`/`Icon size="sm"`.

**Maximize was replaced by Close.** Maximize made sense when Storage was a floating panel
over the 3D view. As a resizable column with a splitter it is redundant — you widen it by
dragging — and it made Files the one panel whose header offered no way to put it away.
Removing the button made the whole `maximized` machinery dead (nothing could set it
true), so the state, its Escape handler, the fixed-overlay styling branch and the
body-portaled scrim went with it: ~40 lines.

Two things had keyed off `maximized` as a proxy for "wide": the Modified column and the
list's fill behaviour. The column now keys off the panel's real width
(`useFilesPanel.width >= 420`) — it is a space question, and the user answers it by
dragging the splitter, rather than by entering a mode that no longer exists.

## "Files" is now "Storage", and its header mirrors the dock tab strip

The panel was labelled Files in the rail while calling itself Storage inside, and the
API, the scopes and the docs all say storage. One name now: **Storage**.

Its header was still built its own way — a title-plus-dropdown row of no fixed height,
sitting one column away from Model and Outliner whose headers are a 32px strip with an
icon+label chip on the left and controls on the right. In the flyout it now uses that
same shape exactly (`h-8 px-1 border-b border-edge`, `gap-1.5` icon+label), so the two
bars share a baseline and a bottom rule — measured identical at top 73, height 32.

The scope picker moved out of the title line onto its own row underneath. A dropdown
wedged into a title bar is what made the header a different height and shape from every
other panel's; below the title it reads as this panel's folder path, which is what it is.

Refresh was the last unaligned icon: a bare `ReloadIcon` at its natural size next to two
16px `Icon`s. All three header icons now measure 14px.

The store, its key (`ada:files-panel:v1`) and the module name stay `filesPanel` — renaming
the persisted key would silently reset every user's panel width and open state, which is
a real cost for no gain that the user can see.

## Add opening / Add equipment are split buttons

Both need a *type* before placement means anything, so the first toolbar version made the
whole button open a type picker. That made every placement cost two clicks and a menu,
including the tenth identical door — a toolbar button that never actually does the thing
it is named after.

They are split buttons now: the icon half fires with the chosen type, a 14px caret beside
it opens the picker. The tooltip names what will be placed (`Add opening: Door (db)`), so
the current type is readable without opening anything.

Three rules, in `src/shell/splitButton.ts` and tested there:

- **A type is chosen** → fire.
- **Nothing chosen yet** → the icon half opens the picker too. Arming to place "nothing"
  is a press with no visible effect, which reads as a broken button.
- **Already armed** → always fire, because a second press disarms. Offering a type picker
  to cancel something answers a question nobody asked.

`chosenTypeLabel` returns null for a slug that is no longer in the catalogue — a model can
be reloaded against a different one while a stale slug sits in the store, and a button
that claims it places a Door and then places nothing is worse than one that admits no type
is chosen.

Two details that are easy to get wrong:

- **The pair needs its own flex box.** The toolbar has `gap-0.5`, so without a wrapper the
  2px gap lands *between* the halves and they read as two adjacent buttons — exactly what
  the squared-off facing corners are trying to deny.
- **The first version built the wrapper as a component defined inside the render loop.**
  A fresh function identity every render means React remounts the subtree, dropping the
  button refs the menu anchors to. Branch on the element, never on a locally-defined
  component type.

`caretClasses()` joins the design system rather than `buttonClasses(...) + "w-3.5"`: the
size classes carry horizontal padding, and `cn` is a plain join, so two conflicting
padding utilities are resolved by stylesheet order rather than by the order written. This
is the third time that has bitten (`w-20` on `Input`, the `Slider` wrapper).

## One scope picker, in Storage

The title bar kept a copy of the scope dropdown after the Storage panel got its own. Two
controls bound to the same store always agree, so the second one teaches you nothing and
still has to be read and ignored. The title-bar copy is gone.

Scope lives where its consequences are: it decides which files exist — upload under one
and they are invisible under another — so it belongs at the top of the list it filters,
the way a folder path does.

The cost is real and worth stating: scope is now only visible while the Storage panel is
open. That is the right trade because scope only matters when files do, but it does mean
the answer to "which project am I in?" moved behind a toggle.

The **page profile keeps its copy**. `/convert` and `/admin` have no rail and no Storage
panel, so removing it there would leave scope with no home at all rather than a quieter
one.

## The type catalogues 404'd in dev, not on this branch

The + Opening and + Equipment pickers showed "No opening types". The cause was not the
toolbar rewrite: `fetchOpeningTypes` / `fetchEquipmentTypes` fire from `openModel` in
`cellBuilderStore`, byte-identical to main, and main's panel has the same empty-list
fallback. Every procedural catalogue endpoint simply 404'd against the dev REST stub,
which also printed eight warnings on every model open.

The stub now answers seven of them: opening, equipment, cell and system types, design
rulesets, engines, and the equipment resync. Everything is tagged `origin: "code"` —
the real API returns the union of code-defined archetypes and the scope's DB entries, and
the code half genuinely is a static list, so the fixture is the honest half rather than an
invention. Tagging anything `"catalog"` would put rows in a database that does not exist
here, and every picker label shows the origin ("Door, single leaf (code)"), so the label
would be lying.

`blueprints` and `detailing-engines` answer with empty lists, and compile still answers
501. Those need a worker; there is nothing truthful a static fixture can return.

**Heredocs ate the regex escapes for the third time** (`\/` → `/`), which produced
`/^/scopes/...` — a broken regex that stopped vite reloading the config, so the endpoints
kept 404ing after the "fix". Use the Edit tool or write a script file; never a heredoc for
anything containing backslash escapes.

## Storage's dialogs are the app's dialogs

Deleting a procedural model asked through `window.confirm`. Thirteen native dialogs were
left in `StorageBrowser`: four deletes, the template-name prompt, and eight alerts for
failures.

They are blocking, unstyleable, and visibly not part of the application — a browser dialog
over a dark themed viewer reads as a different program. In the embed build it is worse:
the dialog carries the HOST page's origin, so a docs page shows "docs.example.com says:
Delete file?", which looks like a phishing attempt. All thirteen now go through
`confirm()` / `promptText()` / `alertText()`.

Two things the conversion improved beyond the chrome:

- **Multi-line messages became real lines.** `previewKeyList` returns a newline-joined
  string that a native dialog rendered as separate lines; in a styled `<p>` it would wrap
  mid-path. It is split into one body line per key.
- **Alerts got titles.** `window.alert(e.message)` gave a bare string with no indication
  of what failed. "Some files could not be moved" over the list says more than the list.

`noNativeDialogs.test.ts` enforces this with an allowlist burn-down, same shape as
`noAdHocChrome`: the seven admin tabs are all that remain, and a second test fails if an
allowlist entry has already been converted — a burn-down list holding converted files
stops measuring anything and quietly re-permits what it names.

## The Scene panel's Clip tab is gone

Section planes are a rail tool with their own strip, so the tab was the same controls a
second time — the fourth duplicated group found this way, after section planes in the mode
strips, groups, and the Results transport. Two places to add a plane means neither is
*the* place.

Folding it away needed more than deleting it. Add / flip / gizmo / clear were already
toolbar buttons, but two things in the tab were not, and would have gone silently:

- **Which plane you are steering.** Flip and the gizmo act on the *active* plane, and with
  several planes there was no way to say which that was.
- **The position slider.** Dragging a plane along its normal without reaching for the 3D
  gizmo is the thing people actually did in that tab.

Both became one control — `SectionPlaneControl` — rather than two: the button names the
active plane and switches between them, the slider moves it. They belong together because
a slider with no label is meaningless in a strip that has no room for one. It renders
nothing at all when there are no planes, rather than showing a dead slider next to the
three buttons that create what it needs.

`ModeTool` gained a `render?: () => ReactNode` escape hatch for this. Deliberately narrow:
a strip of icons cannot express "choose among several of the same object" or "set a
continuous value", and those are the only two cases. A third would be the signal to
promote it into a real toolbar-widget type instead of adding a fourth.

**Cap colour went to Preferences, not the strip.** It is a look you pick once, not a
per-cut action, and a colour well in a toolbar of verbs is the kind of thing that ends up
there because it had nowhere else to go.

The arithmetic moved to `src/shell/sectionRange.ts` with tests. Two things worth pinning:
`position` and `constant` are sign-inverses, and getting that backwards makes the slider
drive the plane the wrong way — visible only in 3D, never in a type. And the range is
padded 10% past the box at both ends, without which the extremes leave the plane exactly
touching the model, so it can never fully clip or fully reveal; the leftover sliver reads
as a broken slider.

The marking menu's "Section planes" entry now arms the clip strip instead of opening a
Scene tab that no longer exists.

## The Simulation panel is plugin-only now

Its built-in content had already been leaving for the toolbar — play, stop, the legend and
the data table went first. What stayed was everything that picks a *value*: field,
component, step, deformation scale, colormap, layer, IP reduction. That left a permanently
docked panel holding one column of dropdowns, spending a quarter of the window on controls
you set once and then leave alone.

Those moved into a **display popover** on the Results toolbar. A popover is the honest
shape for that content: on screen while you set it, gone afterwards, with the 3D getting
the space back. It is not a panel demoted — it is the same controls, hosted where their
lifetime actually is.

**The panel stays registered.** Plugin `fem-sidebar` panels have no other host, so deleting
it outright would drop them silently — inventory row B11 again, the same class of failure
as the four cellbuilder overlays lost with `Menu.tsx`. It is now `available:
hasSimulationContributors` and `defaultOpen: false`, so a stock Results layout has no
Simulation panel and a plugin install still gets one.

Two things the move exposed:

- **The transport was FEA-only.** `togglePlay` and `stopPlayback` required a live FEA
  session, which greyed them out for a GLTF model whose clips were perfectly playable.
  Invisible while the panel carried its own play button; with the panel gone it would have
  meant no way to play a clip at all. They now fall through to the mixer, and `stop` zeroes
  `currentKey` too — the scrubber reads its position from the store, so a stop that only
  told the mixer would leave the slider parked where the clip stopped.
- **A fifth duplicated control group.** The GLTF path had its own play / stop / data
  buttons, drawn differently from the toolbar's. Gone; the clip picker and time scrubber
  stay, because those choose a value.

The clip picker was also the "No animation" dropdown that was unreadable: a fixed `w-60`
with no `min-w-0`, in a row built for a panel as wide as the window, so in a narrow dock
everything competed for one line and the clip name — the thing you actually read — got the
least of it. It is a labelled column now.

**Two traps hit again, both already in this document.** The popover drew transparent, with
the Storage header showing straight through the controls: panel surfaces carry alpha in the
glass presets and CSS cannot flatten it, so it needs an opaque `surface-0` base with the
tinted layer stacked on top. And `panelRegistry` importing the `@/plugins` barrel pulled in
the slot components, which reach stores, which reach the model worker — every test touching
the registry died on `?worker&inline`. Sixth occurrence. Import `@/plugins/registry`
directly.

## The Builder's Tools tab is "Output" now

It had become a bag of five buttons with no subject: Resync equipments, Propose
relocations, Export to Excel, Download IFC, Download Genie XML — plus a CAD checkbox, and
underneath them the things those actions produce.

Split by what each thing *is*, the same cut that dissolved the View tab:

- **Export** is a split button in the Build toolbar, with the format on the caret. Same
  shape as the opening and equipment pickers, for the same reason: exporting the same
  format twice should not cost a menu.
- **Resync** and **Propose relocations** are commands under **Tools** in the menu bar.
  They are occasional and deliberate, and both are named far better by a sentence than by
  an icon nobody would recognise. The IFC-CAD toggle sits with them as a checked command,
  because it changes what the export *produces*, not what you see.
- **What is left** — the relocation proposal you read and then accept, the resync summary,
  the compile log — stays in the tab, which is why it is called Output.

Two things worth recording:

- **`exportFormats()` asks the engine.** Only `adapy-default` compiles a detail or
  simulation model; the others export the workbook alone. The panel did that check inline
  as a JSX condition, which is exactly the kind of rule that evaporates in a move — a
  toolbar offering a Genie XML the engine cannot produce would fail at the worker, not at
  the button. `chosenExportFormat` also returns null for a format the current engine
  cannot make, so switching engines cannot leave the button claiming an IFC it cannot
  produce.
- **The chosen format lives in `src/shell/exportPrefs.ts`, not on `cellBuilderStore`.**
  The store is business logic and off limits to this rebuild, and this genuinely is chrome
  state: it remembers which item of a split button you picked last. The model does not
  care.

`splitButtonState` gained a `noun`, because Export picks a *format*. Hardcoding "type" had
the export button saying "choose a type" — the kind of wrong word that makes a control
read as somebody else's, pasted in.

The tab also carried six dead imports (`PositionedMenu`, `typePickerItems`, `followerUrl`,
`IconOverlaySection`, `Section`, `describeToolState`) left by the earlier split, when
snapping, the follower window and the icon overlay moved out and took their usages with
them. TypeScript does not flag unused imports here, so they had simply sat there.

## No native dialogs left anywhere

The admin tabs were the last seven files on the `noNativeDialogs` allowlist. It is empty
now: twenty-two `confirm` / `prompt` / `alert` calls across nine admin files, plus two in
`WebsocketStatusMenu`, all converted.

**The test was wrong, and finding that was the point.** Its regex matched
`window.confirm(…)` only. A bare `confirm(…)` is the *same global* opening the *same
dialog* — and six of them sat in `CorpusTab`, `StorageTab`, `ProjectsTab` and
`CliTokenButton` while the test reported those files clean. A rule that catches only the
spelling people happened to use is not a rule. It now matches both, with an `EXEMPT`
pattern for our own awaited replacements (the native ones are never awaited — they block)
and a skip for `{/*` JSX comments, which is what made `AppShell` a false positive.

Three of the conversions needed their enclosing function made `async` — `deleteFolder` in
CorpusTab, `onFolderRenameOrMove` in StorageTab, and the CI-bot-token handler in
ProjectsTab. That is the tell that these were never really synchronous decisions: the code
was written around `confirm` blocking the thread, which is the behaviour that makes it
wrong in the first place.

The copy improved on the way through. `window.confirm` takes one string, so everything was
crammed into it with `\n\n` separators — the key preview in CorpusTab's bulk delete, the
scope name in StorageTab's cache clear. Each is now a title and separate body lines, which
is what they were trying to be.
