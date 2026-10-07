// The Sources tab's Options panel: everything about how the tree is filtered and drawn, and what
// its provider is asked for, behind ONE toolbar button -- the toolbar beside the collection picker
// had grown wider than the panel. Each part is a collapsible section; which ones are open is the
// caller's (so a chip under the search box can open the one it describes).

import React from "react";

export interface OptionsSection {
  id: string;
  title: string;
  /** A short note beside the title while something in the section is set, e.g. the filtered provider. */
  badge?: string | null;
  content: React.ReactNode;
}

const TreeOptionsPanel: React.FC<{
  sections: readonly OptionsSection[];
  open: ReadonlySet<string>;
  onToggle: (id: string) => void;
}> = ({ sections, open, onToggle }) => (
  <div className="border-b border-gray-700 text-xs">
    {sections.map((s) => {
      const isOpen = open.has(s.id);
      return (
        <div key={s.id} className="border-t border-gray-700/60 first:border-t-0">
          <button
            type="button"
            aria-expanded={isOpen}
            className="w-full flex items-center gap-1.5 px-2 py-1 text-left text-gray-300 hover:bg-gray-800"
            onClick={() => onToggle(s.id)}
          >
            <span className="w-3 text-gray-500">{isOpen ? "▾" : "▸"}</span>
            <span className="font-medium">{s.title}</span>
            {s.badge && <span className="ml-auto truncate rounded-full bg-blue-900/60 px-1.5 text-[11px] text-blue-100">{s.badge}</span>}
          </button>
          {isOpen && <div className="px-2 pb-2">{s.content}</div>}
        </div>
      );
    })}
  </div>
);

export default TreeOptionsPanel;
