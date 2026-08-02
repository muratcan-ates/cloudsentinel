/* Appearance boot — the palette and the accessibility settings, applied
   before the first paint, on every page of the site.

   The dashboard is a single page, but the console, the handbook and the API
   docs are their own documents. A visitor who picks the paper palette or
   turns on larger text expects that choice to survive walking into one of
   them; before this file they did not, because only app.js knew how to read
   the preference and app.js only runs on the dashboard.

   So the reading and the stamping live here, this file is loaded in <head>
   on all four pages (no inline script — the CSP forbids it, and a script in
   the head applies the attributes before anything is painted, which is what
   keeps the palette from flashing), and app.js drives its switcher through
   the same helpers rather than a second copy of them. */

(function () {
  "use strict";

  var THEMES = ["horizon", "mission", "paper", "dawn", "vivid"];
  var DEFAULT_THEME = "vivid";
  var THEME_KEY = "sentinel-theme";
  var A11Y_KEY = "sentinel-a11y";
  var A11Y_TOGGLES = [
    "font",
    "mask",
    "contrast",
    "links",
    "headings",
    "cursor",
    "motion",
  ];

  /* Storage is a privilege, not a given: private windows and hardened
     browsers throw on access. Every reader below degrades to the default
     rather than taking the page down with it. */
  function read(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function write(key, value) {
    try {
      window.localStorage.setItem(key, value);
      return true;
    } catch (error) {
      return false;
    }
  }

  /* ?theme= wins so a review link opens on the palette it names; otherwise
     the visitor's stored choice, otherwise the house default. */
  function resolveTheme() {
    var param = null;
    try {
      param = new URLSearchParams(window.location.search).get("theme");
    } catch (error) {
      param = null;
    }
    if (THEMES.indexOf(param) !== -1) return param;
    var stored = read(THEME_KEY);
    if (THEMES.indexOf(stored) !== -1) return stored;
    return DEFAULT_THEME;
  }

  function stampTheme(theme) {
    document.documentElement.dataset.theme = theme;
  }

  function storeTheme(theme) {
    return write(THEME_KEY, theme);
  }

  function readA11y() {
    try {
      return JSON.parse(read(A11Y_KEY) || "{}") || {};
    } catch (error) {
      return {};
    }
  }

  /* The attributes only exist while they are doing something, so the CSS
     selectors stay cheap and the DOM stays readable in devtools. */
  function stampA11y(prefs) {
    var root = document.documentElement;
    var scale = typeof prefs.scale === "number" ? prefs.scale : 1;
    var line = typeof prefs.line === "number" ? prefs.line : 1;
    var track = typeof prefs.track === "number" ? prefs.track : 0;

    root.style.setProperty("--a11y-scale", String(scale));
    root.style.setProperty("--a11y-line", String(line));
    root.style.setProperty("--a11y-track", track + "em");
    root.toggleAttribute("data-a11y-scale", scale !== 1);
    root.toggleAttribute("data-a11y-line", line !== 1);
    root.toggleAttribute("data-a11y-track", track !== 0);

    for (var i = 0; i < A11Y_TOGGLES.length; i += 1) {
      var key = A11Y_TOGGLES[i];
      if (prefs[key]) root.setAttribute("data-a11y-" + key, prefs[key]);
      else root.removeAttribute("data-a11y-" + key);
    }
  }

  stampTheme(resolveTheme());
  stampA11y(readA11y());

  window.SentinelAppearance = {
    THEMES: THEMES,
    DEFAULT_THEME: DEFAULT_THEME,
    THEME_KEY: THEME_KEY,
    A11Y_KEY: A11Y_KEY,
    A11Y_TOGGLES: A11Y_TOGGLES,
    resolveTheme: resolveTheme,
    stampTheme: stampTheme,
    storeTheme: storeTheme,
    readA11y: readA11y,
    stampA11y: stampA11y,
  };
})();
