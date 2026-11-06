// Helpers for vite.config.desktop.ts when `ada desktop --dev --backend` points the dev frontend at a
// hosted server. Plain .mjs (no TypeScript) so the Python tests can run it with node.

/**
 * The server's /config.js with every absolute address of the server turned into the dev origin, so the
 * page keeps talking to its own (proxied) origin. The stock server emits a relative `/api`, which needs
 * no change; this guards a deployment that spells out its own address. Other origins (the identity
 * provider) are left alone.
 *
 * @param {string} text   the body of the server's /config.js
 * @param {string} backend the server, e.g. "https://studio.example.com/"
 * @param {string} devOrigin the dev server's origin, e.g. "http://localhost:39000"
 */
export function rewriteConfigJs(text, backend, devOrigin) {
    const b = new URL(backend);
    const d = new URL(devOrigin);
    const ws = (u) => (u.protocol === 'https:' ? 'wss:' : 'ws:');
    return text
        .split(`${b.protocol}//${b.host}`)
        .join(`${d.protocol}//${d.host}`)
        .split(`${ws(b)}//${b.host}`)
        .join(`${ws(d)}//${d.host}`);
}
