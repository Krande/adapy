import React, {useEffect, useRef} from "react";

// Inline name editor used for file/folder rename and new-folder
// creation. Enter commits, Escape (or blur) cancels. ``selectStem``
// pre-selects the basename-without-extension so a quick type replaces
// the name but keeps the extension. Shared by the storage panel and
// the admin corpus tree.
const InlineNameInput: React.FC<{
    initial: string;
    placeholder?: string;
    selectStem?: boolean;
    onCommit: (value: string) => void;
    onCancel: () => void;
}> = ({initial, placeholder, selectStem, onCommit, onCancel}) => {
    const inputRef = useRef<HTMLInputElement>(null);
    useEffect(() => {
        const el = inputRef.current;
        if (!el) return;
        el.focus();
        if (selectStem) {
            const dot = initial.lastIndexOf(".");
            el.setSelectionRange(0, dot > 0 ? dot : initial.length);
        } else {
            el.select();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
        <input
            ref={inputRef}
            type="text"
            defaultValue={initial}
            placeholder={placeholder}
            className={
                "flex-1 min-w-0 bg-gray-800 border border-blue-500 rounded-sm " +
                "px-1 py-0.5 text-xs text-gray-100 focus:outline-hidden"
            }
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
                if (e.key === "Enter") {
                    onCommit((e.target as HTMLInputElement).value);
                } else if (e.key === "Escape") {
                    onCancel();
                }
            }}
            onBlur={onCancel}
        />
    );
};

export default InlineNameInput;
