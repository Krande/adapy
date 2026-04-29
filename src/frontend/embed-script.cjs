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

htmlContent = replacePlaceholderWithTimestamp(htmlContent);
htmlContent = htmlContent.replace(LINK_TAG_REGEX, `<style>\n${cssContent}\n</style>`);
htmlContent = htmlContent.replace(SCRIPT_TAG_REGEX,
    `<script type="module" crossorigin>\n${jsContent}\n</script>`);

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
