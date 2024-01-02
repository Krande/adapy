const fs = require('fs');
const path = require('path');
const archiver = require('archiver');

const distPath = 'dist';
const assetsPath = path.join(distPath, 'assets');
const htmlFilePath = path.join(distPath, 'index.html');
const outputDir = '../../src/ada/visit/resources'; // Set the neighboring directory here

// Function to find the JavaScript file
function findJavaScriptFile() {
    const files = fs.readdirSync(assetsPath);
    return files.find(file => file.startsWith('index-') && file.endsWith('.js'));
}

const jsFileName = findJavaScriptFile();

if (jsFileName) {
    const jsFilePath = path.join(assetsPath, jsFileName);

    // Read the JavaScript file content
    const jsContent = fs.readFileSync(jsFilePath, 'utf8');

    // Read the HTML file content
    let htmlContent = fs.readFileSync(htmlFilePath, 'utf8');

    // Split HTML content around the script tag
    const splitRegex = /(<script type="module" crossorigin src=".\/assets\/index-.*?\.js"><\/script>)/;
    const htmlParts = htmlContent.split(splitRegex);

    if (htmlParts && htmlParts.length === 3) {
        // Reconstruct the HTML with the embedded JavaScript
        const newHtmlContent = htmlParts[0] + `<script type="module" crossorigin>\n${jsContent}\n</script>` + htmlParts[2];
        fs.writeFileSync(htmlFilePath, newHtmlContent);
        console.log("JavaScript embedded successfully.");

        // Zip index.html and move to another folder
        const output = fs.createWriteStream(path.join(outputDir, 'index.zip'));
        const archive = archiver('zip', {
            zlib: {level: 9} // Sets the compression level
        });

        output.on('close', function () {
            console.log(`index.zip was created: ${archive.pointer()} total bytes`);
        });

        archive.on('error', function (err) {
            throw err;
        });

        archive.pipe(output);
        archive.append(newHtmlContent, {name: 'index.html'});
        archive.finalize();

    } else {
        console.log("Script tag not found or multiple instances found.");
    }
} else {
    console.log("JavaScript file not found.");
}

