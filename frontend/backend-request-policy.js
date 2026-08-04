const RENDERER_SECURITY_HEADERS = new Set([
    'x-freelancerstudio-token',
    'x-freelancerstudio-transport',
]);

function authorizeRendererRequest({
    url,
    method,
    requestHeaders,
    webContentsId,
    trustedWebContentsId,
    expectedOrigin,
    backendToken,
}) {
    const headers = { ...(requestHeaders || {}) };
    for (const name of Object.keys(headers)) {
        if (RENDERER_SECURITY_HEADERS.has(name.toLowerCase())) delete headers[name];
    }
    let target;
    try {
        target = new URL(url);
    } catch {
        return headers;
    }
    const targetBackendOrigin = target.protocol === 'ws:'
        ? `http://${target.host}`
        : target.origin;
    let normalizedPath = target.pathname;
    try {
        for (let attempt = 0; attempt < 3 && /%[0-9a-f]{2}/i.test(normalizedPath); attempt += 1) {
            normalizedPath = decodeURIComponent(normalizedPath);
        }
    } catch {
        return headers;
    }
    if (/%[0-9a-f]{2}/i.test(normalizedPath)) return headers;
    const protectedTestLabPath = normalizedPath.startsWith('/api/sandbox-test-lab/');
    const trustedTarget = webContentsId === trustedWebContentsId
        && targetBackendOrigin === expectedOrigin
        && !protectedTestLabPath
        && (normalizedPath.startsWith('/api/') || normalizedPath.startsWith('/ws/'));
    if (!trustedTarget || !backendToken) return headers;

    headers['X-FreelancerStudio-Token'] = backendToken;
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(String(method || '').toUpperCase())) {
        headers.Origin = expectedOrigin;
        if (!headers['Content-Type'] && !headers['content-type']) {
            headers['Content-Type'] = 'application/json';
        }
    }
    return headers;
}

module.exports = {
    RENDERER_SECURITY_HEADERS,
    authorizeRendererRequest,
};
