import React from "react";
import {
    Badge,
    Button,
    Checkbox,
    Field,
    Icon,
    ICON_NAMES,
    IconButton,
    Input,
    Kbd,
    Panel,
    PanelBody,
    PanelFooter,
    PanelHeader,
    PropertyRow,
    SegmentedControl,
    Section,
    Select,
    Splitter,
    StatusDot,
    Switch,
    TabPanel,
    Tabs,
    Textarea,
    ToggleButton,
    Toolbar,
    ToolbarGroup,
    ToolbarSeparator,
    ToolbarSpacer,
    Tooltip,
    type ButtonSize,
    type ButtonVariant,
    type Tone,
} from "@/components/ui";
import {THEME_PRESETS, useThemeStore, type ThemePresetId} from "@/state/themeStore";

// Live catalogue of the design system, mounted at `?uikit=1`.
//
// The point is comparison, not documentation: every variant of every primitive on one
// page, so an inconsistency between (say) a secondary Button and a Select at the same
// size is visible side by side rather than discovered later across two panels. The
// theme switcher at the top re-paints the whole page, which is how the token layer
// gets checked against all four presets in seconds.
//
// It renders NOTHING from the real app and the real app renders nothing from it —
// reviewing this carries no regression risk.

const VARIANTS: ButtonVariant[] = ["primary", "secondary", "ghost", "danger", "subtle"];
const SIZES: ButtonSize[] = ["sm", "md", "lg"];
const TONES: Tone[] = ["neutral", "accent", "pass", "warn", "fail", "info"];

function Row({label, children}: {label: string; children: React.ReactNode}) {
    return (
        <div className="flex items-center gap-3 flex-wrap min-h-control-lg">
            <span className="w-24 shrink-0 text-xs text-content-subtle font-mono">{label}</span>
            {children}
        </div>
    );
}

function Card({title, children}: {title: string; children: React.ReactNode}) {
    return (
        <Panel className="min-w-0">
            <PanelHeader title={title} />
            <PanelBody className="flex flex-col gap-3">{children}</PanelBody>
        </Panel>
    );
}

export default function UiGallery() {
    const preset = useThemeStore((s) => s.preset);
    const setPreset = useThemeStore((s) => s.setPreset);
    const bgOpacity = useThemeStore((s) => s.bgOpacity);
    const setBgOpacity = useThemeStore((s) => s.setBgOpacity);

    const [tab, setTab] = React.useState("build");
    const [seg, setSeg] = React.useState<"solid" | "wire" | "xray">("solid");
    const [checked, setChecked] = React.useState(true);
    const [indet, setIndet] = React.useState(true);
    const [on, setOn] = React.useState(true);
    const [splitW, setSplitW] = React.useState(220);

    return (
        <div className="h-full w-full overflow-auto scrollbar bg-surface-0 text-content font-ui text-base">
            <div className="max-w-[1400px] mx-auto p-6 flex flex-col gap-6">
                {/* ---- theme switcher -------------------------------------- */}
                <Panel>
                    <PanelHeader
                        title="ada design system"
                        subtitle="Every primitive, every variant. Switch preset to check the token layer."
                        actions={<Badge tone="accent">?uikit=1</Badge>}
                    />
                    <PanelBody className="flex flex-wrap items-center gap-4">
                        <SegmentedControl
                            label="Panel theme preset"
                            value={preset}
                            onChange={(p) => setPreset(p as ThemePresetId)}
                            options={Object.entries(THEME_PRESETS).map(([id, v]) => ({
                                value: id,
                                label: v.name,
                            }))}
                        />
                        <label className="flex items-center gap-2 text-sm text-content-muted">
                            background opacity
                            <input
                                type="range"
                                min={0.1}
                                max={1}
                                step={0.05}
                                value={bgOpacity}
                                onChange={(e) => setBgOpacity(Number(e.target.value))}
                                className="ada-focus"
                            />
                            <span className="font-mono text-xs tabular-nums w-8">{bgOpacity.toFixed(2)}</span>
                        </label>
                    </PanelBody>
                </Panel>

                <div className="grid gap-6 [grid-template-columns:repeat(auto-fit,minmax(360px,1fr))]">
                    {/* ---- buttons ----------------------------------------- */}
                    <Card title="Button">
                        {VARIANTS.map((v) => (
                            <Row key={v} label={v}>
                                {SIZES.map((s) => (
                                    <Button key={s} variant={v} size={s}>
                                        {s === "sm" ? "Small" : s === "md" ? "Medium" : "Large"}
                                    </Button>
                                ))}
                                <Button variant={v} disabled>
                                    Disabled
                                </Button>
                                <Button variant={v} loading>
                                    Loading
                                </Button>
                            </Row>
                        ))}
                        <Row label="with icon">
                            <Button variant="primary" iconLeft={<Icon name="upload" size="sm" />}>
                                Upload
                            </Button>
                            <Button iconRight={<Icon name="chevron" size="sm" />}>Next</Button>
                            <Button variant="danger" iconLeft={<Icon name="close" size="sm" />}>
                                Delete
                            </Button>
                        </Row>
                        <Row label="block">
                            <Button block variant="primary">
                                Full width
                            </Button>
                        </Row>
                    </Card>

                    {/* ---- icon buttons ------------------------------------ */}
                    <Card title="IconButton & Toolbar">
                        {SIZES.map((s) => (
                            <Row key={s} label={s}>
                                <IconButton size={s} tooltip="Move" icon={<Icon name="move" size={s === "lg" ? "lg" : "md"} />} />
                                <IconButton size={s} tooltip="Rotate" icon={<Icon name="rotate" size={s === "lg" ? "lg" : "md"} />} />
                                <IconButton size={s} tooltip="Scale" pressed icon={<Icon name="scale" size={s === "lg" ? "lg" : "md"} />} />
                                <IconButton size={s} variant="secondary" tooltip="Settings" icon={<Icon name="settings" />} />
                                <IconButton size={s} variant="danger" tooltip="Close" icon={<Icon name="close" />} />
                            </Row>
                        ))}
                        <Row label="toolbar">
                            <Toolbar label="Gallery demo toolbar" className="w-full bg-surface-2 border border-edge rounded-md px-1.5 py-1">
                                <ToolbarGroup>
                                    <IconButton tooltip="Undo" icon={<Icon name="undo" />} />
                                    <IconButton tooltip="Redo" icon={<Icon name="redo" />} />
                                </ToolbarGroup>
                                <ToolbarSeparator />
                                <ToolbarGroup>
                                    <IconButton tooltip="Move" pressed icon={<Icon name="move" />} />
                                    <IconButton tooltip="Rotate" icon={<Icon name="rotate" />} />
                                    <IconButton tooltip="Scale" icon={<Icon name="scale" />} />
                                </ToolbarGroup>
                                <ToolbarSpacer />
                                <ToggleButton pressed size="sm" variant="ghost">
                                    Snap
                                </ToggleButton>
                            </Toolbar>
                        </Row>
                        <Row label="tooltip">
                            <Tooltip content="Portal-rendered, escapes overflow, keyboard-triggered">
                                <Button variant="secondary">Hover or focus me</Button>
                            </Tooltip>
                        </Row>
                    </Card>

                    {/* ---- form controls ----------------------------------- */}
                    <Card title="Form controls">
                        <Row label="input">
                            <Input fieldSize="sm" placeholder="Small" className="w-28" />
                            <Input placeholder="Medium" className="w-32" />
                            <Input fieldSize="lg" placeholder="Large" className="w-32" />
                        </Row>
                        <Row label="mono">
                            <Input mono defaultValue="IPE300" className="w-32" />
                            <Input mono defaultValue="12.750" className="w-24" />
                        </Row>
                        <Row label="states">
                            <Input placeholder="Disabled" disabled className="w-32" />
                            <Input defaultValue="Invalid" aria-invalid className="w-32" />
                        </Row>
                        <Row label="select">
                            <Select fieldSize="sm" className="w-28" defaultValue="glb">
                                <option value="glb">GLB</option>
                                <option value="ifc">IFC</option>
                                <option value="step">STEP</option>
                            </Select>
                            <Select className="w-32" defaultValue="viridis">
                                <option value="viridis">viridis</option>
                                <option value="jet">jet</option>
                                <option value="coolwarm">coolwarm</option>
                            </Select>
                        </Row>
                        <Row label="textarea">
                            <Textarea placeholder="Notes…" className="w-full" />
                        </Row>
                        <Field label="Section name" hint="Shown in the outliner" required>
                            <Input placeholder="e.g. Deck level 2" />
                        </Field>
                        <Field label="Scale factor" error="Must be a positive number">
                            <Input defaultValue="-1" />
                        </Field>
                    </Card>

                    {/* ---- toggles ----------------------------------------- */}
                    <Card title="Checkbox & Switch">
                        <Checkbox label="Geometry edges" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                        <Checkbox
                            label="Auto fit to view"
                            hint="Frames the model after every load"
                            checked={indet}
                            indeterminate={indet}
                            onChange={(e) => setIndet(e.target.checked)}
                        />
                        <Checkbox label="Disabled option" disabled />
                        <div className="h-px bg-edge my-1" />
                        <Switch label="On-demand render" hint="Only redraw when something changes" checked={on} onChange={(e) => setOn(e.target.checked)} />
                        <Switch label="Adaptive DPR while orbiting" defaultChecked={false} />
                        <Switch label="Disabled switch" disabled />
                        <div className="h-px bg-edge my-1" />
                        <Section title="Property rows">
                            <PropertyRow label="Point size" hint="Absolute units">
                                <Input fieldSize="sm" mono defaultValue="1.0" className="w-16" />
                            </PropertyRow>
                            <PropertyRow label="Material mode">
                                <Select fieldSize="sm" className="w-28" defaultValue="std">
                                    <option value="std">Standard</option>
                                    <option value="basic">Basic</option>
                                </Select>
                            </PropertyRow>
                            <PropertyRow label="Show colour legend">
                                <Switch defaultChecked />
                            </PropertyRow>
                        </Section>
                    </Card>

                    {/* ---- tabs -------------------------------------------- */}
                    <Card title="Tabs & SegmentedControl">
                        <Row label="underline">
                            <div className="w-full">
                                <Tabs
                                    label="Gallery demo tabs"
                                    value={tab}
                                    onChange={setTab}
                                    items={[
                                        {id: "build", label: "Build", badge: 12},
                                        {id: "systems", label: "Systems", badge: 3},
                                        {id: "view", label: "View"},
                                        {id: "fem", label: "FEM", contextual: true},
                                        {id: "joints", label: "Joints", contextual: true, badge: "!"},
                                        {id: "off", label: "Disabled", disabled: true},
                                    ]}
                                />
                                <TabPanel id={tab} active className="p-2 text-sm text-content-muted">
                                    Panel for <span className="font-mono text-content">{tab}</span>. Arrow keys move between tabs.
                                </TabPanel>
                            </div>
                        </Row>
                        <Row label="pill">
                            <Tabs
                                label="Pill variant"
                                variant="pill"
                                value={tab}
                                onChange={setTab}
                                items={[
                                    {id: "build", label: "Build"},
                                    {id: "systems", label: "Systems"},
                                    {id: "view", label: "View"},
                                ]}
                            />
                        </Row>
                        <Row label="segmented">
                            <Tabs
                                label="Segmented variant"
                                variant="segmented"
                                value={tab}
                                onChange={setTab}
                                items={[
                                    {id: "build", label: "Build"},
                                    {id: "systems", label: "Systems"},
                                    {id: "view", label: "View"},
                                ]}
                            />
                        </Row>
                        <Row label="control">
                            <SegmentedControl
                                label="Display mode"
                                value={seg}
                                onChange={setSeg}
                                options={[
                                    {value: "solid", label: "Solid"},
                                    {value: "wire", label: "Wireframe"},
                                    {value: "xray", label: "X-ray"},
                                ]}
                            />
                        </Row>
                    </Card>

                    {/* ---- status ------------------------------------------ */}
                    <Card title="Badge, StatusDot, Kbd">
                        <Row label="badge">
                            {TONES.map((t) => (
                                <Badge key={t} tone={t}>
                                    {t}
                                </Badge>
                            ))}
                        </Row>
                        <Row label="with dot">
                            {TONES.map((t) => (
                                <Badge key={t} tone={t} dot>
                                    {t}
                                </Badge>
                            ))}
                        </Row>
                        <Row label="dots">
                            {TONES.map((t) => (
                                <StatusDot key={t} tone={t} label={t} />
                            ))}
                            <StatusDot tone="warn" label="running" pulse />
                        </Row>
                        <Row label="kbd">
                            <span className="flex items-center gap-1 text-sm text-content-muted">
                                <Kbd>Shift</Kbd>+<Kbd>A</Kbd> zoom to fit
                            </span>
                            <span className="flex items-center gap-1 text-sm text-content-muted">
                                <Kbd>Ctrl</Kbd>+<Kbd>K</Kbd> commands
                            </span>
                        </Row>
                    </Card>

                    {/* ---- panel ------------------------------------------- */}
                    <Card title="Panel anatomy">
                        <Panel elevation="flat" className="h-56">
                            <PanelHeader
                                title="Selected object"
                                subtitle="BM2_3 · IPE300"
                                actions={
                                    <>
                                        <IconButton size="sm" tooltip="Pin panel" icon={<Icon name="pin" size="sm" />} />
                                        <IconButton size="sm" tooltip="Float panel" icon={<Icon name="float" size="sm" />} />
                                        <IconButton size="sm" tooltip="Close panel" icon={<Icon name="close" size="sm" />} />
                                    </>
                                }
                            />
                            <PanelBody className="flex flex-col gap-2">
                                {/* Enough rows to prove the body scrolls under a sticky header. */}
                                {Array.from({length: 12}, (_, i) => (
                                    <PropertyRow key={i} label={`Property ${i + 1}`}>
                                        <span className="font-mono text-sm text-content-muted tabular-nums">
                                            {(i * 12.5).toFixed(2)}
                                        </span>
                                    </PropertyRow>
                                ))}
                            </PanelBody>
                            <PanelFooter>
                                <Button size="sm" variant="subtle">
                                    Reset
                                </Button>
                                <Button size="sm" variant="primary">
                                    Apply
                                </Button>
                            </PanelFooter>
                        </Panel>
                    </Card>

                    {/* ---- splitter ---------------------------------------- */}
                    <Card title="Splitter">
                        <p className="text-xs text-content-subtle">
                            Drag the handle, or focus it and use arrow keys / Home / End. This is what the dock layout
                            rests on — the canvas reflows instead of being covered.
                        </p>
                        <div className="flex h-40 border border-edge rounded-md overflow-hidden">
                            <div
                                style={{width: splitW}}
                                className="shrink-0 bg-surface-2 p-2 text-xs text-content-muted overflow-hidden"
                            >
                                left dock — {Math.round(splitW)}px
                            </div>
                            <Splitter
                                orientation="vertical"
                                label="Resize left dock"
                                value={splitW}
                                onChange={setSplitW}
                                min={120}
                                max={420}
                            />
                            <div className="flex-1 min-w-0 bg-surface-1 p-2 text-xs text-content-muted">viewport</div>
                        </div>
                    </Card>

                    {/* ---- icons ------------------------------------------- */}
                    <Card title={`Icons (${ICON_NAMES.length})`}>
                        <div className="grid gap-1 [grid-template-columns:repeat(auto-fill,minmax(84px,1fr))]">
                            {ICON_NAMES.map((n) => (
                                <div
                                    key={n}
                                    className="flex flex-col items-center gap-1 p-2 rounded-sm pointer-fine:hover:bg-surface-2"
                                    title={n}
                                >
                                    <Icon name={n} size="lg" />
                                    <span className="text-[10px] text-content-subtle truncate w-full text-center">{n}</span>
                                </div>
                            ))}
                        </div>
                    </Card>

                    {/* ---- surfaces ---------------------------------------- */}
                    <Card title="Surfaces & text steps">
                        <div className="flex flex-col gap-2">
                            {/* Written out rather than built with `bg-${s}`: Tailwind extracts
                                class names statically from source, so an interpolated class
                                never gets generated. */}
                            {([
                                ["surface-0", "bg-surface-0"],
                                ["surface-1", "bg-surface-1"],
                                ["surface-2", "bg-surface-2"],
                                ["surface-3", "bg-surface-3"],
                            ] as const).map(([name, cls]) => (
                                <div key={name} className={`${cls} border border-edge rounded-md p-2 text-sm`}>
                                    <span className="font-mono text-xs text-content-subtle">{name}</span>
                                </div>
                            ))}
                            <div className="flex flex-col gap-1 pt-2">
                                <span className="text-content">content — primary text</span>
                                <span className="text-content-muted">content-muted — labels</span>
                                <span className="text-content-subtle">content-subtle — hints</span>
                            </div>
                            <div className="flex items-center gap-2 pt-2">
                                <span className="w-6 h-6 rounded-sm bg-select" title="--ada-select" />
                                <span className="text-sm text-content-muted">
                                    <span className="font-mono">--ada-select</span> — same value the three.js highlight uses
                                </span>
                            </div>
                        </div>
                    </Card>
                </div>
            </div>
        </div>
    );
}
