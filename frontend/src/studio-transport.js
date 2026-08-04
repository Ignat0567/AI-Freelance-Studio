const nativeFetch = window.fetch.bind(window);
const NativeWebSocket = window.WebSocket;

function sameOriginStudioUrl(value) {
    const parsed = new URL(value, window.location.href);
    if (
        parsed.protocol === 'http:'
        && parsed.hostname === 'localhost'
        && parsed.port === window.location.port
        && parsed.pathname.startsWith('/api/')
    ) {
        return `${window.location.origin}${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
    if (
        parsed.protocol === 'ws:'
        && parsed.hostname === 'localhost'
        && parsed.port === window.location.port
        && parsed.pathname.startsWith('/ws/')
    ) {
        return `ws://${window.location.host}${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
    return parsed.href;
}

window.fetch = (input, init) => {
    if (typeof input === 'string' || input instanceof URL) {
        return nativeFetch(sameOriginStudioUrl(String(input)), init);
    }
    if (input instanceof Request) {
        return nativeFetch(new Request(sameOriginStudioUrl(input.url), input), init);
    }
    return nativeFetch(input, init);
};

window.WebSocket = function StudioWebSocket(url, protocols) {
    const normalized = sameOriginStudioUrl(String(url));
    return protocols === undefined
        ? new NativeWebSocket(normalized)
        : new NativeWebSocket(normalized, protocols);
};
window.WebSocket.prototype = NativeWebSocket.prototype;
Object.setPrototypeOf(window.WebSocket, NativeWebSocket);
