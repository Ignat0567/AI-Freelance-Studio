const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const packageMetadata = require('../package.json');

const filename = `AI Freelance Studio-Setup-${packageMetadata.version}-win.exe`;
const installerPath = path.join(__dirname, '..', 'installers', filename);
if (!fs.existsSync(installerPath)) {
    console.error(`Installer not found: ${filename}`);
    process.exit(1);
}

const digest = crypto.createHash('sha256').update(fs.readFileSync(installerPath)).digest('hex').toUpperCase();
fs.writeFileSync(`${installerPath}.sha256`, `${digest} *${filename}\n`, 'ascii');
console.log(`SHA-256 written for ${filename}: ${digest}`);
