/* keymap.js — global keyboard shortcuts.
 *
 * Loaded by base.html (defer). Installs a single keydown listener on
 * window and dispatches to handlers. Two-key sequences (e.g. "g d") use
 * a 1.5-second timeout to reset state.
 *
 * Shortcuts:
 *   ?         show/hide keyboard help overlay
 *   /         focus the page's primary search input
 *   g d       go to dashboard (/)
 *   g a       go to /apps
 *   g u       go to /users
 *   g k       go to /api-keys
 *   g s       go to /admin
 *   Escape    close help overlay or open cmdk modal
 *
 * Shortcuts do not fire while the user is typing in an <input>,
 * <textarea>, or [contenteditable], EXCEPT when a modifier key
 * (Ctrl/Meta/Alt) is involved.
 */
(function () {
  "use strict";

  var SHORTCUTS = [
    ["?",     "Show this help"],
    ["/",     "Focus search"],
    ["g d",   "Go to Dashboard"],
    ["g a",   "Go to Apps"],
    ["g u",   "Go to Users"],
    ["g k",   "Go to API Keys"],
    ["g s",   "Go to Admin"],
    ["Cmd/Ctrl+K", "Open command palette"],
    ["Esc",   "Close overlay"],
  ];

  var GO_TARGETS = {
    "d": "/",
    "a": "/apps",
    "u": "/users",
    "k": "/api-keys",
    "s": "/admin",
  };

  var pendingGo = false;     // True after user pressed "g" — wait for next key.
  var pendingTimer = null;

  function resetPending() {
    pendingGo = false;
    if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null; }
  }

  function armPending() {
    pendingGo = true;
    if (pendingTimer) clearTimeout(pendingTimer);
    pendingTimer = setTimeout(resetPending, 1500);
  }

  function isTypingTarget(el) {
    if (!el) return false;
    var tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function buildHelpOverlay() {
    var mount = document.getElementById("kbd-help-mount");
    if (!mount) return null;
    var existing = mount.querySelector(".kbd-help");
    if (existing) return existing;

    var overlay = document.createElement("div");
    overlay.className = "kbd-help";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "Keyboard shortcuts");
    overlay.hidden = true;

    var panel = document.createElement("div");
    panel.className = "kbd-help-panel";

    var title = document.createElement("div");
    title.className = "kbd-help-title";
    title.textContent = "Keyboard Shortcuts";
    panel.appendChild(title);

    var list = document.createElement("dl");
    list.className = "kbd-help-list";
    SHORTCUTS.forEach(function (row) {
      var dt = document.createElement("dt");
      // Wrap each token in <kbd>
      row[0].split(" ").forEach(function (token, i) {
        if (i > 0) dt.appendChild(document.createTextNode(" "));
        var kbd = document.createElement("kbd");
        kbd.textContent = token;
        dt.appendChild(kbd);
      });
      var dd = document.createElement("dd");
      dd.textContent = row[1];
      list.appendChild(dt);
      list.appendChild(dd);
    });
    panel.appendChild(list);

    var hint = document.createElement("div");
    hint.className = "kbd-help-hint";
    hint.textContent = "Press ? again or Esc to close.";
    panel.appendChild(hint);

    overlay.appendChild(panel);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) hideHelp();
    });
    mount.appendChild(overlay);
    return overlay;
  }

  function isHelpOpen() {
    var mount = document.getElementById("kbd-help-mount");
    if (!mount) return false;
    var overlay = mount.querySelector(".kbd-help");
    return !!(overlay && !overlay.hidden);
  }

  function showHelp() {
    var overlay = buildHelpOverlay();
    if (overlay) overlay.hidden = false;
  }

  function hideHelp() {
    var mount = document.getElementById("kbd-help-mount");
    if (!mount) return;
    var overlay = mount.querySelector(".kbd-help");
    if (overlay) overlay.hidden = true;
  }

  function toggleHelp() {
    if (isHelpOpen()) hideHelp(); else showHelp();
  }

  function focusSearch() {
    var input = document.querySelector('input[type="search"], input[name="q"]');
    if (input) {
      input.focus();
      try { input.select(); } catch (e) {}
      return true;
    }
    return false;
  }

  function closeCmdkIfOpen() {
    if (window.cmdk && typeof window.cmdk.close === "function") {
      return window.cmdk.close();
    }
    return false;
  }

  function onKeyDown(e) {
    // Always allow Escape to close overlays, even from inputs.
    if (e.key === "Escape") {
      var closedCmdk = closeCmdkIfOpen();
      if (isHelpOpen()) { hideHelp(); e.preventDefault(); return; }
      if (closedCmdk) { e.preventDefault(); return; }
      resetPending();
      return;
    }

    var hasModifier = e.ctrlKey || e.metaKey || e.altKey;
    if (isTypingTarget(e.target) && !hasModifier) return;

    // Shift is fine — many shortcuts (like "?") require it.

    if (e.key === "?") {
      e.preventDefault();
      resetPending();
      toggleHelp();
      return;
    }

    if (e.key === "/") {
      // Don't intercept while typing.
      e.preventDefault();
      resetPending();
      focusSearch();
      return;
    }

    // Two-key "g <x>" sequences.
    if (pendingGo) {
      var k = (e.key || "").toLowerCase();
      var target = GO_TARGETS[k];
      resetPending();
      if (target) {
        e.preventDefault();
        window.location.href = target;
      }
      return;
    }

    if (e.key === "g" || e.key === "G") {
      // Don't fire inside text fields.
      armPending();
      return;
    }
  }

  function init() {
    // Pre-build the overlay shell so first "?" press feels instant.
    buildHelpOverlay();
    window.addEventListener("keydown", onKeyDown);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.keymap = {
    showHelp: showHelp,
    hideHelp: hideHelp,
    toggleHelp: toggleHelp,
  };
})();
