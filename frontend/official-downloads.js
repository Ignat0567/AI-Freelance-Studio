const OFFICIAL_DOWNLOAD_URLS = Object.freeze({
    python: 'https://www.python.org/downloads/windows/',
    pip: 'https://docs.python.org/3/library/ensurepip.html',
    nodejs: 'https://nodejs.org/en/download',
    npm: 'https://nodejs.org/en/download',
    opencode: 'https://opencode.ai/docs/',
    git: 'https://git-scm.com/download/win',
    'github-cli': 'https://cli.github.com/',
    vscode: 'https://code.visualstudio.com/download',
    pycharm: 'https://www.jetbrains.com/pycharm/download/',
    'android-platform-tools': 'https://developer.android.com/tools/releases/platform-tools',
    docker: 'https://www.docker.com/products/docker-desktop/',
    ollama: 'https://ollama.com/download/windows',
    expo: 'https://docs.expo.dev/get-started/set-up-your-environment/',
    ios_simulator: 'https://developer.apple.com/xcode/resources/'
});

const ALLOWED_DOWNLOAD_ORIGINS = new Set([
    'https://www.python.org',
    'https://docs.python.org',
    'https://nodejs.org',
    'https://opencode.ai',
    'https://git-scm.com',
    'https://cli.github.com',
    'https://code.visualstudio.com',
    'https://www.jetbrains.com',
    'https://developer.android.com',
    'https://www.docker.com',
    'https://ollama.com',
    'https://docs.expo.dev',
    'https://developer.apple.com'
]);

function officialDownloadUrl(downloadId) {
    if (typeof downloadId !== 'string' || !Object.prototype.hasOwnProperty.call(OFFICIAL_DOWNLOAD_URLS, downloadId)) return '';
    const target = new URL(OFFICIAL_DOWNLOAD_URLS[downloadId]);
    if (target.protocol !== 'https:' || !ALLOWED_DOWNLOAD_ORIGINS.has(target.origin)) return '';
    return target.toString();
}

function isAllowedOfficialUrl(rawUrl) {
    if (typeof rawUrl !== 'string') return false;
    try {
        const target = new URL(rawUrl);
        if (target.protocol !== 'https:' || !ALLOWED_DOWNLOAD_ORIGINS.has(target.origin)) return false;
        return Object.values(OFFICIAL_DOWNLOAD_URLS).some(url => new URL(url).toString() === target.toString());
    } catch {
        return false;
    }
}

function isAllowedLoopbackUrl(rawUrl) {
    if (typeof rawUrl !== 'string') return false;
    try {
        const target = new URL(rawUrl);
        const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname);
        return loopback && ['http:', 'https:'].includes(target.protocol) && !target.username && !target.password;
    } catch {
        return false;
    }
}

function classifyWindowOpenUrl(rawUrl) {
    if (isAllowedOfficialUrl(rawUrl)) return 'official';
    if (isAllowedLoopbackUrl(rawUrl)) return 'loopback';
    return 'reject';
}

function isAllowedStudioNavigation(rawUrl, expectedOrigin) {
    if (typeof rawUrl !== 'string' || !expectedOrigin) return false;
    try {
        const target = new URL(rawUrl);
        return ['http:', 'https:'].includes(target.protocol) && !target.username && !target.password && target.origin === expectedOrigin;
    } catch {
        return false;
    }
}

function isAllowedDownloadSender(event, mainWebContents, expectedOrigin) {
    if (!event || !mainWebContents || event.sender !== mainWebContents) return false;
    if (!event.senderFrame || event.senderFrame !== mainWebContents.mainFrame) return false;
    try {
        return new URL(event.senderFrame.url).origin === expectedOrigin;
    } catch {
        return false;
    }
}

module.exports = {
    OFFICIAL_DOWNLOAD_URLS,
    ALLOWED_DOWNLOAD_ORIGINS,
    officialDownloadUrl,
    isAllowedOfficialUrl,
    isAllowedLoopbackUrl,
    classifyWindowOpenUrl,
    isAllowedStudioNavigation,
    isAllowedDownloadSender,
};
