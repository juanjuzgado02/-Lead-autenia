// Configuration for API endpoints
// If VITE_API_URL is set (e.g. in production), use it.
// Otherwise, default to empty string which means relative paths (proxied in dev).

export const API_BASE_URL = import.meta.env.VITE_API_URL || '';

export const getApiUrl = (path) => {
    if (path.startsWith('http')) return path;
    // Ensure path starts with / if not present
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${API_BASE_URL}${normalizedPath}`;
};

/**
 * Autenia runs this as a private, single-user internal tool: the server holds
 * every credential in its own .env and the browser never sees one. That is the
 * default in this fork. Set VITE_AUTENIA_INTERNAL_MODE=false at build time to
 * get the upstream OpenShorts bring-your-own-key behaviour back.
 *
 * Must match AUTENIA_INTERNAL_MODE on the backend — the backend is what
 * actually enforces this; the flag only decides what the UI offers.
 */
export const INTERNAL_MODE =
    import.meta.env.VITE_AUTENIA_INTERNAL_MODE !== 'false';

// localStorage entries that held API keys in the bring-your-own-key build.
const LEGACY_KEY_STORAGE = [
    'gemini_key',
    'uploadPostKey_v3',
    'elevenLabsKey_v1',
    'falKey_v1',
];

/**
 * Delete any API key left in this browser by an earlier BYOK build.
 *
 * These are deliberately not migrated to the server: a key that sat in
 * localStorage — possibly on a synced profile — should be rotated at the
 * provider, not moved. Clearing them only removes the copy we control.
 * Returns the names that were present, so the UI can tell the user to rotate.
 */
export const purgeLegacyBrowserKeys = () => {
    if (!INTERNAL_MODE) return [];
    const found = LEGACY_KEY_STORAGE.filter((name) => localStorage.getItem(name));
    found.forEach((name) => localStorage.removeItem(name));
    return found;
};
