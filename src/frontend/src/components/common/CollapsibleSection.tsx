import React, {useState} from "react";
import ChevronRightIcon from "@/components/icons/ChevronRightIcon";

interface CollapsibleSectionProps {
    title: string;
    defaultOpen?: boolean;
    children: React.ReactNode;
    headerClassName?: string;
    bodyClassName?: string;
}

// Lightweight accordion section used inside the Scene panel (and any
// future container panel) to group related sub-content under a
// click-to-collapse header. Uncontrolled by design — each section owns
// its own open state. Promote to a controlled prop if a future caller
// needs to drive it from outside.
const CollapsibleSection: React.FC<CollapsibleSectionProps> = ({
    title,
    defaultOpen = true,
    children,
    headerClassName = "",
    bodyClassName = "",
}) => {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="border-t border-white/20 first:border-t-0">
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className={`w-full flex items-center gap-1 py-1 text-left font-bold select-none ${headerClassName}`.trim()}
                aria-expanded={open}
            >
                <ChevronRightIcon
                    className={`transition-transform shrink-0 ${open ? "rotate-90" : ""}`}
                />
                <span>{title}</span>
            </button>
            {open && (
                <div className={`pb-1 ${bodyClassName}`.trim()}>{children}</div>
            )}
        </div>
    );
};

export default CollapsibleSection;
