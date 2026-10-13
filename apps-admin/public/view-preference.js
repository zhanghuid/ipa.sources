/**
 * Remembers small view choices (platform tab, device filter) across reloads.
 *
 * The admin page and the catalog page share the same keys, so switching
 * platform in one place carries over to the other. Storage access is wrapped
 * because browsers block localStorage when cookies are disabled or in some
 * private modes; a blocked store must not break the page.
 */
(function () {
  const PREFIX = "ipa-sources:";

  function read(key, allowed, fallback) {
    try {
      const value = localStorage.getItem(PREFIX + key);
      return allowed.includes(value) ? value : fallback;
    } catch {
      return fallback;
    }
  }

  function write(key, value) {
    try {
      localStorage.setItem(PREFIX + key, value);
    } catch {
      // Persisting the choice is a convenience; ignore unavailable storage.
    }
  }

  window.viewPreference = { read, write };
})();
