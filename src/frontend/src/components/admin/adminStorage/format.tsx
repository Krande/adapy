import React from "react";
import {formatBytes as formatBytesBase} from "@/utils/format";

export const Td: React.FC<{children: React.ReactNode; title?: string}> = ({children, title}) => (
    <td className="px-3 py-2 truncate" title={title}>
        {children}
    </td>
);

// Render an ISO-shaped UTC string in the browser's local timezone.
// The "sv-SE" locale gives the same "YYYY-MM-DD HH:MM:SS" layout the
// raw-ISO slice used to produce, but with the values shifted to the
// viewer's wall clock — matches what an Oslo admin actually expects.
export function fmtIsoLocal(ts: string | null | undefined): string {
    if (!ts) return "—";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleString("sv-SE");
}

export const formatBytes = (n: number) => formatBytesBase(n, {emptyOnZero: true});

export function suggestedName(sourceKey: string, target: string): string {
    const base = sourceKey.replace(/\.[^./]+$/, "");
    return `${base}.${target}`;
}
