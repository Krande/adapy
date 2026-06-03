import React, {useState} from "react";

const ShortcutsModal: React.FC = () => {
    const [open, setOpen] = useState(false);
    return (
        <div>
            <button
                className="bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded-sm w-full"
                onClick={() => setOpen(!open)}
            >
                Shortcut Keys
            </button>
            {open && (
                <div className="mt-2 bg-gray-700 p-2 rounded-sm text-xs space-y-1">
                    <p><kbd>Shift + H</kbd>: Hide</p>
                    <p><kbd>Shift + U</kbd>: Unhide All</p>
                    <p><kbd>Shift + F</kbd>: Center on Selection</p>
                    <p><kbd>Shift + A</kbd>: Zoom to All</p>
                    <p><kbd>Shift + Q</kbd>: Toggle Options Menu</p>
                    <p><kbd>Shift + T</kbd>: Toggle Selection Tree</p>
                    <p><kbd>Shift + C</kbd>: Copy Selection to Clipboard</p>
                </div>
            )}
        </div>
    );
};

export default ShortcutsModal;
