/**
 * Remembers small view choices (platform tab, device filter) across reloads.
 *
 * The admin page and the catalog page share the same keys, so switching
 * platform in one place carries over to the other. Storage access is wrapped
 * because browsers block localStorage when cookies are disabled or in some
 * private modes; a blocked store must not break the page.
 *
 * This file loads synchronously in <head>, before the first paint, and copies
 * the saved choice onto <html> as data-platform / data-device. CSS highlights
 * the matching tab from that attribute immediately. Applying the choice from
 * the deferred page scripts instead painted the default tab for a frame and
 * then jumped to the saved one, which looked like a flicker.
 *
 * Each page declares, in markup, the key it renders (data-<key> holds the
 * default) and the values it accepts (data-<key>-options). Reading the allowed
 * set from markup keeps the vocabulary next to the buttons it describes, so
 * this file stays generic and a stored value the page no longer renders falls
 * back to the default before the first paint instead of selecting nothing.
 */
(function () {
  const PREFIX = "ipa-sources:";
  const MIRRORED_KEYS = ["platform", "device"];
  const root = document.documentElement;

  function optionsFor(key) {
    return (root.getAttribute(`data-${key}-options`) || "").split(/\s+/).filter(Boolean);
  }

  /** Put the effective value on <html> so CSS can select it before first paint. */
  function applyStoredChoice(key) {
    const fallback = root.getAttribute(`data-${key}`) || "";
    const options = optionsFor(key);
    let value = fallback;
    try {
      const saved = localStorage.getItem(PREFIX + key);
      if (saved && options.includes(saved)) value = saved;
    } catch {
      // Storage is unavailable; the markup default still paints correctly.
    }
    if (value) root.setAttribute(`data-${key}`, value);
  }

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
    // Keep the pre-paint attribute in step when the choice changes in page.
    if (MIRRORED_KEYS.includes(key) && value) root.setAttribute(`data-${key}`, value);
  }

  for (const key of MIRRORED_KEYS) applyStoredChoice(key);

  window.viewPreference = { read, write };
})();
