/* harness.js — small UI helpers shared across pages.
 * Loaded once from base.html; everything attaches to window.harness.
 *
 * Public surface:
 *   harness.showToast(msg, type, durationMs)
 *       type: 'info' | 'success' | 'warn' | 'error'  (default 'info')
 *       durationMs: number ms (default 3000); 0 = stay until clicked.
 *   harness.setButtonLoading(buttonEl, isLoading, loadingText?)
 *   harness.applyPalette(name)   // 'slate' | 'forest' | 'monochrome' | '' (default)
 *   harness.applyTheme(name)     // 'dark' | 'light'
 *
 * Also: alert() is monkey-patched at the bottom of this file to route
 * through showToast so legacy inline code keeps working without rewriting
 * every call site.
 */
(function () {
  "use strict";

  // ── Theme + palette ─────────────────────────────────────────────────────
  function applyPalette(name) {
    if (name) {
      document.documentElement.setAttribute("data-palette", name);
    } else {
      document.documentElement.removeAttribute("data-palette");
    }
    try { localStorage.setItem("palette", name || ""); } catch (e) {}
    syncSwatches();
  }
  function applyTheme(name) {
    document.documentElement.setAttribute("data-theme", name || "dark");
    try { localStorage.setItem("theme", name || "dark"); } catch (e) {}
  }
  function syncSwatches() {
    var current = "";
    try { current = localStorage.getItem("palette") || ""; } catch (e) {}
    document.querySelectorAll(".swatch[data-palette]").forEach(function (btn) {
      var isActive = (btn.dataset.palette || "") === current;
      btn.classList.toggle("swatch--active", isActive);
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  // ── Toasts ──────────────────────────────────────────────────────────────
  function showToast(message, type, durationMs) {
    type = type || "info";
    durationMs = durationMs === undefined ? 3000 : durationMs;
    var container = document.getElementById("toast-container");
    if (!container) {
      // Last-resort fallback when called before DOMContentLoaded
      console.log("[toast:" + type + "]", message);
      return;
    }
    var toast = document.createElement("div");
    toast.className = "toast toast--" + type;
    toast.setAttribute("role", type === "error" ? "alert" : "status");

    var msgSpan = document.createElement("span");
    msgSpan.className = "toast-msg";
    msgSpan.textContent = String(message);

    var closeBtn = document.createElement("button");
    closeBtn.className = "toast-close";
    closeBtn.setAttribute("aria-label", "Dismiss");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", function () { dismiss(); });

    toast.appendChild(msgSpan);
    toast.appendChild(closeBtn);
    container.appendChild(toast);

    var timer = null;
    function dismiss() {
      if (timer) clearTimeout(timer);
      toast.classList.add("toast--leaving");
      setTimeout(function () { toast.remove(); }, 180);
    }
    if (durationMs > 0) {
      timer = setTimeout(dismiss, durationMs);
    }
    return { dismiss: dismiss };
  }

  // ── Button loading state ───────────────────────────────────────────────
  function setButtonLoading(btn, isLoading, loadingText) {
    if (!btn) return;
    if (isLoading) {
      if (btn._origText === undefined) btn._origText = btn.textContent;
      btn.disabled = true;
      btn.classList.add("btn--loading");
      if (loadingText) btn.textContent = loadingText;
    } else {
      btn.disabled = false;
      btn.classList.remove("btn--loading");
      if (btn._origText !== undefined) {
        btn.textContent = btn._origText;
        delete btn._origText;
      }
    }
  }

  // ── Inline field validation ────────────────────────────────────────────
  // Any <input data-validate="..."> gets blur-time validity checks. A
  // <small class="field-error"> message is inserted after the input and
  // cleared on the next valid blur. The data-validate tokens (required,
  // minlength, email, url, pattern) just gate which custom-friendly text
  // we use; the browser's native ValidityState is the source of truth.
  function friendlyMessage(input) {
    var v = input.validity;
    var tokens = (input.dataset.validate || "").split(/\s+/).filter(Boolean);
    var has = function (t) { return tokens.indexOf(t) !== -1; };
    if (v.valueMissing && has("required")) return "This field is required.";
    if (v.typeMismatch && has("email"))    return "Enter a valid email address.";
    if (v.typeMismatch && has("url"))      return "Enter a valid URL (https://…).";
    if (v.tooShort && has("minlength")) {
      return "Must be at least " + input.minLength + " characters.";
    }
    if (v.patternMismatch && has("pattern")) {
      return input.title || "Value doesn't match the required format.";
    }
    // Fall back to the browser-provided string when no friendly mapping fits.
    return input.validationMessage || "Invalid value.";
  }

  function clearFieldError(input) {
    var sib = input.nextElementSibling;
    if (sib && sib.classList && sib.classList.contains("field-error")) {
      sib.remove();
    }
    input.classList.remove("input--invalid");
    input.removeAttribute("aria-invalid");
  }

  function setFieldError(input, message) {
    clearFieldError(input);
    var msg = document.createElement("small");
    msg.className = "field-error";
    msg.textContent = message;
    input.classList.add("input--invalid");
    input.setAttribute("aria-invalid", "true");
    input.parentNode.insertBefore(msg, input.nextSibling);
  }

  function validateField(input) {
    if (input.disabled || input.readOnly) return;
    if (input.checkValidity()) {
      clearFieldError(input);
    } else {
      setFieldError(input, friendlyMessage(input));
    }
  }

  function wireValidators(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-validate]").forEach(function (el) {
      if (el._validatorWired) return;
      el._validatorWired = true;
      el.addEventListener("blur", function () { validateField(el); });
      el.addEventListener("input", function () {
        // Clear stale errors as the user types a fix — re-validate on next blur.
        if (el.classList.contains("input--invalid") && el.checkValidity()) {
          clearFieldError(el);
        }
      });
    });
  }

  // ── Boot ────────────────────────────────────────────────────────────────
  function init() {
    // Restore palette + theme from localStorage. The theme is also restored
    // by the inline <script> in <head> for anti-flash; this is the late-bind
    // for the palette (less critical — palettes only override the accent).
    try {
      var palette = localStorage.getItem("palette") || "";
      if (palette) document.documentElement.setAttribute("data-palette", palette);
    } catch (e) {}

    // Wire palette swatches if present
    document.querySelectorAll(".swatch[data-palette]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        applyPalette(btn.dataset.palette || "");
      });
    });
    syncSwatches();

    // Wire blur validators for any [data-validate] inputs on the page.
    wireValidators(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // ── Patch window.alert to route through toasts ─────────────────────────
  // Legacy inline JS in dashboard.html / apps.html / app_form.html calls
  // alert() directly. Routing those through showToast() gives a consistent
  // non-blocking UX without rewriting every site.
  var nativeAlert = window.alert;
  window.alert = function (message) {
    if (document.getElementById("toast-container")) {
      showToast(message, "error", 6000);
    } else {
      nativeAlert(message);
    }
  };

  window.harness = {
    showToast: showToast,
    setButtonLoading: setButtonLoading,
    applyPalette: applyPalette,
    applyTheme: applyTheme,
    wireValidators: wireValidators,
    validateField: validateField,
  };
})();
