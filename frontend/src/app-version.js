const buildVersion = import.meta.env.VITE_APP_VERSION;

function cleanVersion(value) {
    return typeof value === 'string' ? value.trim() : '';
}

export async function getAppVersion() {
    if (window.env?.getAppVersion) {
        try {
            return cleanVersion(await window.env.getAppVersion());
        } catch {
            return '';
        }
    }
    return cleanVersion(buildVersion);
}

export function versionLabel(version) {
    const clean = cleanVersion(version);
    return clean ? `Version ${clean}` : 'Version unavailable';
}
