// Stable per-device identifier so audit logs (frontend load + render metrics)
// from different devices are distinguishable — e.g. an old phone vs a desktop —
// rather than being attributed to the same user indiscriminately.
//
// Persisted in localStorage; survives reloads, regenerates only if storage is
// cleared. It is a random UUID, NOT tied to any hardware identifier, so it
// carries no PII beyond "same browser profile on this device".
const DEVICE_ID_KEY = "ada_device_id";

export function getDeviceId(): string {
    try {
        let id = localStorage.getItem(DEVICE_ID_KEY);
        if (!id) {
            id =
                typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
                    ? crypto.randomUUID()
                    : `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
            localStorage.setItem(DEVICE_ID_KEY, id);
        }
        return id;
    } catch {
        // private mode / storage disabled: a per-session id is still better than none
        return "unknown";
    }
}
