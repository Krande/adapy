// Inlines the vite-emitted entry JS and stylesheet into dist/index.html so
// the Python package's offline viewer can ship a single self-contained file
// (zipped to ../ada/visit/rendering/resources/index.zip).
//
// The entry JS and CSS file names come from the *HTML itself* — vite can
// emit several `index-*.js` chunks (e.g. a worker bootstrap alongside the
// app entry), so picking the alphabetically-first one off the filesystem
// would silently inline the wrong file.

const fs = require('fs');
const path = require('path');
const archiver = require('archiver');

const distPath = 'dist';
const assetsPath = path.join(distPath, 'assets');
const htmlFilePath = path.join(distPath, 'index.html');
const outputDir = '../ada/visit/rendering/resources';

// vite emits absolute URLs (`/assets/...`) when build.base = '/' and
// relative URLs (`./assets/...`) when base is empty/relative — accept both.
const SCRIPT_TAG_REGEX = /<script type="module" crossorigin src="(\.?\/assets\/(index-[^"]+\.js))"><\/script>/;
const LINK_TAG_REGEX = /<link rel="stylesheet" crossorigin href="(\.?\/assets\/(index-[^"]+\.css))">/;

function replacePlaceholderWithTimestamp(content) {
    const timestamp = Date.now();
    return content.replace(/<!--UNIQUE_VERSION_PLACEHOLDER-->/g,
        `<script>window.UNIQUE_VERSION_ID = ${timestamp};</script>`);
}

let htmlContent = fs.readFileSync(htmlFilePath, 'utf8');

const scriptMatch = htmlContent.match(SCRIPT_TAG_REGEX);
const linkMatch = htmlContent.match(LINK_TAG_REGEX);

if (!scriptMatch || !linkMatch) {
    console.log("Entry script or stylesheet tag not found in dist/index.html — skipping inline.");
    process.exit(0);
}

const jsFileName = scriptMatch[2];   // e.g. index-DNVnpj1A.js
const cssFileName = linkMatch[2];

const jsContent = fs.readFileSync(path.join(assetsPath, jsFileName), 'utf8');
const cssContent = fs.readFileSync(path.join(assetsPath, cssFileName), 'utf8');

// Escape any in-bundle `</script>` / `</style>` substrings — minified
// JS regularly contains them as plain string literals (React's internal
// tag-building code is a known offender). Inlining as-is closes the
// outer <script> tag early, the rest of the JS gets parsed as HTML,
// and stray script-tag literals inside the JS get fetched as real
// `/assets/index-*.js` requests that 404 because we shipped a
// single-file bundle. The `<\/...` form is invisible to the JS parser
// but no longer matches the HTML tokenizer's end-tag rule.
function escapeScriptClosers(content) {
    return content.replace(/<\/(script|style)/gi, '<\\/$1');
}

htmlContent = replacePlaceholderWithTimestamp(htmlContent);
// Use the function form of replace so `$1`, `$2`, `$&` in the inlined
// JS / CSS aren't interpreted as backreferences to the matched script
// or link tag's capture groups. Minified user code (regex helpers,
// React internals) routinely contains those tokens as plain strings;
// the string-form of replace would expand them and inject literal
// `<script src="/assets/index-XXX.js">` markers into the bundle, which
// the browser then parses as real script tags and 404s on.
const inlinedStyle = `<style>\n${escapeScriptClosers(cssContent)}\n</style>`;
const inlinedScript = `<script type="module" crossorigin>\n${escapeScriptClosers(jsContent)}\n</script>`;
htmlContent = htmlContent.replace(LINK_TAG_REGEX, () => inlinedStyle);
htmlContent = htmlContent.replace(SCRIPT_TAG_REGEX, () => inlinedScript);

fs.writeFileSync(htmlFilePath, htmlContent);
console.log(`JavaScript (${jsFileName}) and CSS (${cssFileName}) embedded successfully.`);

const output = fs.createWriteStream(path.join(outputDir, 'index.zip'));
const archive = archiver('zip', {zlib: {level: 9}});

output.on('close', () => {
    console.log(`index.zip was created: ${archive.pointer()} total bytes`);
});
archive.on('error', (err) => { throw err; });
archive.pipe(output);
archive.append(htmlContent, {name: 'index.html'});
archive.finalize();
