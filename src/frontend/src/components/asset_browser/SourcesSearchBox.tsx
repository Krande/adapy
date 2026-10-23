import React, { useMemo, useRef, useState } from "react";

import { RECENT_SEARCHES_MAX, loadRecent, recentFor, saveRecent, withRecent } from "@/assets/recentSearches";
import { MIN_SEARCH_CHARS, isSearchTerm } from "@/assets/rowFacts";

// The Sources tab's search box, with the last few searches offered when it is focused. A term is
// remembered when it is committed -- Enter, or leaving the box with a search in it -- not on every
// keystroke, so the list holds what was searched for and not its prefixes.

const SourcesSearchBox: React.FC<{
  value: string;
  onChange: (term: string) => void;
  className: string;
}> = ({ value, onChange, className }) => {
  const [recent, setRecent] = useState<string[]>(() => loadRecent());
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const input = useRef<HTMLInputElement>(null);

  const offered = useMemo(() => (open ? recentFor(recent, value) : []), [open, recent, value]);

  const remember = (term: string) => {
    if (!isSearchTerm(term)) return;
    const next = withRecent(recent, term);
    setRecent(next);
    saveRecent(next);
  };

  const pick = (term: string) => {
    onChange(term);
    remember(term);
    setOpen(false);
    setActive(-1);
    input.current?.focus();
  };

  return (
    <div className="relative">
      <div className={`${className} flex items-center gap-2 px-2`}>
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" className="text-gray-400 shrink-0">
          <circle cx="7" cy="7" r="4.5" />
          <path d="M10.5 10.5 14 14" />
        </svg>
        <input
          ref={input}
          aria-label="Search assets"
          aria-autocomplete="list"
          aria-expanded={offered.length > 0}
          className="flex-1 min-w-0 bg-transparent outline-none text-[13px] text-gray-100 placeholder:text-gray-500"
          placeholder="Search names and refs"
          title={`Case-insensitive; searches from ${MIN_SEARCH_CHARS} characters. Filters the loaded rows (branches are fetched a level at a time as they are opened); matches in unopened branches are listed below the tree from the scope's index. Your last ${RECENT_SEARCHES_MAX} searches drop down when the box is focused`}
          value={value}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            remember(value);
            setOpen(false);
            setActive(-1);
          }}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && offered.length) {
              e.preventDefault();
              setActive((i) => (i + 1) % offered.length);
            } else if (e.key === "ArrowUp" && offered.length) {
              e.preventDefault();
              setActive((i) => (i <= 0 ? offered.length - 1 : i - 1));
            } else if (e.key === "Enter") {
              if (active >= 0 && offered[active]) pick(offered[active]);
              else {
                remember(value);
                setOpen(false);
              }
            } else if (e.key === "Escape") {
              setOpen(false);
              setActive(-1);
            }
          }}
        />
      </div>
      {offered.length > 0 && (
        <ul
          role="listbox"
          aria-label="Recent searches"
          className="absolute left-0 right-0 z-20 mt-1 rounded-sm border border-gray-700 bg-gray-900 py-1 text-[12px] shadow-lg"
        >
          <li className="px-2 pb-0.5 text-[10px] uppercase tracking-wide text-gray-500">Recent searches</li>
          {offered.map((term, i) => (
            <li
              key={term}
              role="option"
              aria-selected={i === active}
              className={`cursor-pointer truncate px-2 py-0.5 ${i === active ? "bg-gray-700 text-gray-100" : "text-gray-300 hover:bg-gray-800"}`}
              title={term}
              // mousedown, not click: the input's blur would close the list before a click lands.
              onMouseDown={(e) => {
                e.preventDefault();
                pick(term);
              }}
            >
              {term}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default SourcesSearchBox;
