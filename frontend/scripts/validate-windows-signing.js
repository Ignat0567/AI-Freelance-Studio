const windowsLink = process.env.WIN_CSC_LINK || '';
const windowsPassword = process.env.WIN_CSC_KEY_PASSWORD || '';
const genericLink = process.env.CSC_LINK || '';
const genericPassword = process.env.CSC_KEY_PASSWORD || '';

const hasLink = Boolean(windowsLink || genericLink);
const hasPassword = Boolean(windowsPassword || genericPassword);

if (hasLink !== hasPassword) {
    console.error('Windows signing configuration is incomplete. Set both certificate link and password variables, or unset all signing variables.');
    process.exit(1);
}

console.log(hasLink
    ? 'Windows signing configuration is complete; electron-builder signing is enabled.'
    : 'Windows signing variables are absent; building unsigned artifacts.');
