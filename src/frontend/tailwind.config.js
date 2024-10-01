/** @type {import('tailwindcss').Config} */
module.exports = {
    mode: "jit",
    content: ["src/**/*.tsx", "./src/index.html"],
    darkMode: "class",
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

