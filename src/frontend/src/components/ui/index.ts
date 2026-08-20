// The design system's public surface. Import from "@/components/ui", never from the
// individual files, so the set stays discoverable and the barrel is the one place to
// see what exists before hand-rolling something.
//
// Enforcement: src/__tests__/ui/noAdHocChrome.test.ts fails any file under
// src/components/** (outside ui/) that hardcodes a palette colour and is not on the
// shrinking allowlist.

export {cn, type ClassValue} from "./cn";

export {
    Button,
    caretClasses, buttonClasses,
    IconButton,
    ToggleButton,
    Spinner,
    BUTTON_BASE,
    type ButtonProps,
    type ButtonVariant,
    type ButtonSize,
    type IconButtonProps,
    type ToggleButtonProps,
} from "./Button";

export {Input,
    fieldClasses, Textarea, Field, FIELD_BASE, type InputProps, type TextareaProps, type FieldProps, type FieldSize} from "./Input";
export {Select, type SelectProps} from "./Select";
export {Slider, type SliderProps} from "./Slider";
export {Checkbox, Switch, type CheckboxProps, type SwitchProps} from "./Checkbox";

export {SegmentedControl, type SegmentedControlProps, type SegmentedOption} from "./SegmentedControl";
export {Tabs, TabPanel, type TabsProps, type TabItem, type TabsVariant} from "./Tabs";

export {Panel, PanelHeader, PanelBody, PanelFooter, Section, PropertyRow, type PanelProps, type PanelHeaderProps} from "./Panel";
export {Toolbar, ToolbarGroup, ToolbarSeparator, ToolbarSpacer, type ToolbarProps} from "./Toolbar";

export {Tooltip, type TooltipProps} from "./Tooltip";
export {Splitter, type SplitterProps} from "./Splitter";
export {EmptyState, Ui} from "./EmptyState";
export {Badge, StatusDot, Kbd, type BadgeProps, type Tone} from "./Badge";
export {Dialog, type DialogProps} from "./Dialog";
export {CollapsibleSection, type CollapsibleSectionProps} from "./CollapsibleSection";
export {default as ConfirmHost} from "./ConfirmHost";

export {Icon, ICONS, ICON_NAMES, type IconName, type IconProps, type IconSize} from "@/components/icons";
