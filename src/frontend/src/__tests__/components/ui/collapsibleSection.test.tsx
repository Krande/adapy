import assert from "node:assert/strict";
import {test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

import ChevronRightIcon from "@/components/icons/ChevronRightIcon";
import LegacyPath from "@/components/common/CollapsibleSection";
import {CollapsibleSection} from "@/components/ui/CollapsibleSection";

// Each variant replaced a disclosure a panel wrote itself. The legacy markup is
// rendered verbatim beside the shared component and must be the same string.

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const noop = () => {};

/** The Options panel's local disclosure, as it was. */
function LegacyOptionsSection({title, open, children}: {title: string; open: boolean; children: React.ReactNode}) {
    return (
        <div>
            <button
                type="button"
                className="w-full flex items-center justify-between py-1 text-left font-semibold"
                onClick={noop}
                aria-expanded={open}
            >
                <span>{title}</span>
                <span className="text-gray-300 text-xs">{open ? "▾" : "▸"}</span>
            </button>
            {open && <div className="mt-2">{children}</div>}
        </div>
    );
}

/** `common/CollapsibleSection` (the Scene panel's), as it was. */
function LegacySceneSection({
    title,
    open,
    headerClassName = "",
    bodyClassName = "",
    children,
}: {
    title: string;
    open: boolean;
    headerClassName?: string;
    bodyClassName?: string;
    children: React.ReactNode;
}) {
    return (
        <div className="border-t border-white/20 first:border-t-0">
            <button
                type="button"
                onClick={noop}
                className={`w-full flex items-center gap-1 py-1 text-left font-bold select-none ${headerClassName}`.trim()}
                aria-expanded={open}
            >
                <ChevronRightIcon className={`transition-transform shrink-0 ${open ? "rotate-90" : ""}`} />
                <span>{title}</span>
            </button>
            {open && <div className={`pb-1 ${bodyClassName}`.trim()}>{children}</div>}
        </div>
    );
}

test("the caret variant is the Options panel's section, closed and open", () => {
    for (const open of [false, true]) {
        assert.equal(
            html(
                <CollapsibleSection title="Theme" variant="caret" defaultOpen={open}>
                    <p>body</p>
                </CollapsibleSection>,
            ),
            html(
                <LegacyOptionsSection title="Theme" open={open}>
                    <p>body</p>
                </LegacyOptionsSection>,
            ),
        );
    }
});

test("the section variant is the Scene panel's section, with and without extra classes", () => {
    for (const open of [false, true]) {
        assert.equal(
            html(
                <CollapsibleSection title="Groups" defaultOpen={open}>
                    <p>body</p>
                </CollapsibleSection>,
            ),
            html(
                <LegacySceneSection title="Groups" open={open}>
                    <p>body</p>
                </LegacySceneSection>,
            ),
        );
        assert.equal(
            html(
                <CollapsibleSection title="Groups" defaultOpen={open} headerClassName="text-xs" bodyClassName="pl-2">
                    <p>body</p>
                </CollapsibleSection>,
            ),
            html(
                <LegacySceneSection title="Groups" open={open} headerClassName="text-xs" bodyClassName="pl-2">
                    <p>body</p>
                </LegacySceneSection>,
            ),
        );
    }
});

test("the section variant opens by default, and the old import path is the shared component", () => {
    assert.match(html(<CollapsibleSection title="A">x</CollapsibleSection>), /aria-expanded="true"/);
    assert.equal(LegacyPath, CollapsibleSection);
});
