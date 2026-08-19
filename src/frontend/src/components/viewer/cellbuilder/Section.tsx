import React from "react";

// A collapsible sub-section — the ▸ chevron idiom used throughout the panel.
// Long or occasional groups default closed so the panel stays short.
export const Section: React.FC<{
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}> = ({ title, count, defaultOpen = false, children }) => {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <div className="border border-gray-600/50 rounded-md bg-black/10 overflow-hidden">
      <button
        className="flex items-center gap-1.5 w-full text-left px-2 py-1.5 hover:bg-gray-700/40"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span
          className={
            "text-gray-400 text-[10px] transition-transform " +
            (open ? "rotate-90" : "")
          }
        >
          ▸
        </span>
        <span className="font-semibold">{title}</span>
        {count != null && (
          <span className="text-gray-400 ml-auto">({count})</span>
        )}
      </button>
      {open && (
        <div className="px-2 pb-2 pt-0.5 flex flex-col gap-2">{children}</div>
      )}
    </div>
  );
};
