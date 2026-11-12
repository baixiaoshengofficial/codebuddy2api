export function readPreference(key, fallback) {
  try {
    return localStorage.getItem(`codebuddy2api.${key}`) ?? fallback;
  } catch {
    return fallback;
  }
}
export function writePreference(key, value) {
  try {
    localStorage.setItem(`codebuddy2api.${key}`, value);
  } catch {
    /* The interface remains usable when optional storage is blocked. */
  }
}
