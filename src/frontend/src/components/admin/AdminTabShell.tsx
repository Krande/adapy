import React from "react";

// The frame every admin tab draws: a title/actions row, an error row, an
// optional loading row, and a scrolling body — in exactly the markup and
// class names the tabs already use, so a tab migrated onto it renders the
// same strings in the same layout.
//
//   <div class="flex flex-col h-full">
//     <div class="flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-gray-700 text-xs"> …header… </div>
//     {subheader}
//     <div class="px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700"> error </div>
//     <div class="flex-1 min-h-0 overflow-auto"> body </div>
//     {footer}
//   </div>

export interface AdminTabShellProps {
    /** Left side of the header row. */
    title?: React.ReactNode;
    /** Right side of the header row (buttons, toggles). Pushed right only when
     * a child carries `ml-auto`, exactly as the tabs lay it out today. */
    actions?: React.ReactNode;
    /** Omit the header row entirely (a tab with its own form on top). */
    header?: React.ReactNode | false;
    headerClassName?: string;
    /** Rows between the header and the error row (filter bars, override
     * panels, forms). */
    subheader?: React.ReactNode;
    error?: string | null;
    errorClassName?: string;
    /** Rendered as a row above the body while true. */
    loading?: boolean;
    loadingLabel?: React.ReactNode;
    /** Renders the shared refresh button in the actions slot. */
    onRefresh?: () => void;
    refreshTitle?: string;
    bodyClassName?: string;
    footer?: React.ReactNode;
    className?: string;
    children?: React.ReactNode;
}

export const ADMIN_TAB_HEADER_CLASS =
    "flex items-center gap-2 px-3 sm:px-4 py-2 border-b border-gray-700 text-xs";
export const ADMIN_TAB_ERROR_CLASS =
    "px-3 sm:px-4 py-2 text-red-300 text-xs border-b border-gray-700";
export const ADMIN_TAB_BODY_CLASS = "flex-1 min-h-0 overflow-auto";

/** The refresh button the storage tab draws: inline spinner, label that stays
 * "Refresh" so a re-tap can abort+retry, big tap target on mobile. */
export const RefreshButton: React.FC<{
    onClick: () => void;
    loading?: boolean;
    title?: string;
    className?: string;
}> = ({onClick, loading = false, title, className = "ml-auto"}) => (
    <button
        type="button"
        className={
            className + " inline-flex items-center gap-1.5 bg-blue-700 active:bg-blue-800 " +
            "hover:bg-blue-600 rounded-sm text-xs " +
            "px-4 py-2 sm:px-3 sm:py-1 min-h-[40px] sm:min-h-0 " +
            "focus:outline-hidden focus:ring-2 focus:ring-blue-400 " +
            (loading ? "opacity-90 cursor-wait" : "")
        }
        onClick={onClick}
        aria-busy={loading}
        title={title ?? (loading ? "Refreshing — tap again to retry" : "Refresh")}
    >
        <svg
            className={"h-3 w-3 " + (loading ? "animate-spin" : "opacity-60")}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
        >
            <path d="M21 12a9 9 0 1 1-3.5-7.1"/>
            <polyline points="21 4 21 10 15 10"/>
        </svg>
        Refresh
    </button>
);

const AdminTabShell: React.FC<AdminTabShellProps> = ({
    title,
    actions,
    header,
    headerClassName = ADMIN_TAB_HEADER_CLASS,
    subheader,
    error,
    errorClassName = ADMIN_TAB_ERROR_CLASS,
    loading,
    loadingLabel = "Loading…",
    onRefresh,
    refreshTitle,
    bodyClassName = ADMIN_TAB_BODY_CLASS,
    footer,
    className = "flex flex-col h-full",
    children,
}) => {
    const headerRow = header === false
        ? null
        : header !== undefined
            ? header
            : (title !== undefined || actions !== undefined || onRefresh) ? (
                <div className={headerClassName}>
                    {title}
                    {actions}
                    {onRefresh && (
                        <RefreshButton onClick={onRefresh} loading={loading} title={refreshTitle}/>
                    )}
                </div>
            ) : null;
    return (
        <div className={className}>
            {headerRow}
            {subheader}
            {error && <div className={errorClassName}>{error}</div>}
            {loading && loadingLabel !== null && (
                <div className="px-3 sm:px-4 py-2 text-gray-500 text-xs">{loadingLabel}</div>
            )}
            <div className={bodyClassName}>{children}</div>
            {footer}
        </div>
    );
};

export default AdminTabShell;
