/** @type {import('tailwindcss').Config} */
module.exports = {
    mode: "jit",
    content: ["src/**/*.tsx", "./src/index.html"],
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

