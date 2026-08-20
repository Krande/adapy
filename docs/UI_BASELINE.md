
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
