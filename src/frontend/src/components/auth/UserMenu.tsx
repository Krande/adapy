import React, {useState} from "react";
import {getUser, isAuthEnabled, signOut} from "@/services/auth/oidc";

// Tiny user pill in the menu bar. Hidden when auth is disabled.
const UserMenu: React.FC = () => {
    const [open, setOpen] = useState(false);
    if (!isAuthEnabled()) return null;
    const user = getUser();
    const label = user.email || user.name || user.sub || "signed in";
    return (
        <div className="relative">
            <button
                className="bg-blue-700 hover:bg-blue-700/50 text-white px-3 py-2 rounded text-xs"
                onClick={() => setOpen((v) => !v)}
                title={label}
            >
                {label}
            </button>
            {open && (
                <div className="absolute right-0 mt-1 w-40 rounded bg-gray-800 text-white text-xs shadow-lg">
                    <div className="px-3 py-2 border-b border-gray-700 truncate" title={label}>
                        {label}
                    </div>
                    <button
                        className="w-full text-left px-3 py-2 hover:bg-gray-700"
                        onClick={() => {
                            void signOut();
                        }}
                    >
                        Sign out
                    </button>
                </div>
            )}
        </div>
    );
};

export default UserMenu;
