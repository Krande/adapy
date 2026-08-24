/** @type {import('tailwindcss').Config} */
module.exports = {
    mode: "jit",
    // packages/plugins/** is in here because the JIT only emits the classes it can
    // SEE. A build-time plugin overlay (see scripts/gen-plugin-registry.mjs) drops a
    // package into packages/plugins/ that is compiled into the bundle like any other
    // source — but it was invisible to this scan, so every class name that appeared
    // only in a plugin was silently dropped from the stylesheet. That is not a new
    // requirement of any one plugin; it has been latent for every plugin shipped so far.
    content: ["src/**/*.tsx", "./src/index.html", "packages/plugins/**/*.{ts,tsx}"],
    darkMode: "class",
    // Only apply `hover:` styles on devices that actually have hover (mouse/trackpad).
    // Without this, tapping a button on touch devices fires :hover and the highlight
    // stays "stuck" until the user taps elsewhere.
    future: {
        hoverOnlyWhenSupported: true,
    },
    theme: {
        extend: {
            gridTemplateRows: {
                mainpage: "6rem minmax(0, 3fr) 2rem"
            },
            colors: {
                'bl-background': '#393939',
            }
        }
    },
    plugins: [],
}

