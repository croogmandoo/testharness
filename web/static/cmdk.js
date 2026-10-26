/* cmdk.js — Cmd+K / Ctrl+K command palette.
 *
 * Loaded by base.html (defer). Mounts a modal into <div id="cmdk-mount">
 * when first opened. Results draw from two sources:
 *
 *   1. Static routes (Dashboard, Apps, Users, etc.)
 *   2. Dynamic app list — fetched once from GET /api/apps (session/
 *      api-key auth, returns app summary rows for the default
 *      environment). The endpoint returns rows of the form:
 *        { app, total, passing, failing, unknown,
 *          last_run, last_run_id, active_run_id }
 *      For brand-new installs where no run has been recorded yet the
 *      endpoint returns [] — we then fall back to scraping
 *      [data-app-name] / .app-tile-name from the current DOM so the
 *      palette is still useful on the first visit.
 *
 * Keyboard:
 *   Cmd/Ctrl+K   open
 *   Esc          close
 *   ↑/↓          move active result
 *   Enter        navigate to active result
 *
 * No external libraries. Plain substring fuzzy filter — characters of
 * the query must appear, in order, somewhere in the candidate label.
 */
(function () {
  "use strict";

  var STATIC_ROUTES = [
    { label: "Dashboard",     url: "/",              hint: "Status" },
    { label: "Apps",          url: "/apps",          hint: "Manage" },
    { label: "Users",         url: "/users",         hint: "Manage" },
    { label: "API Keys",      url: "/api-keys",      hint: "Manage" },
    { label: "Admin",         url: "/admin",         hint: "Settings" },
    { label: "Audit Log",     url: "/admin/audit",   hint: "Admin" },
    { label: "Runners",       url: "/admin/runners", hint: "Admin" },
    { label: "CA Certs",      url: "/admin/ca-certs",hint: "Admin" },
    { label: "LDAP Settings", url: "/admin/ldap",    hint: "Admin" },
    { label: "Proxy Settings",url: "/admin/proxy",   hint: "Admin" },
    { label: "Secrets",       url: "/secrets",       hint: "Manage" },
    { label: "Settings",      url: "/admin/config",  hint: "Admin" },
  ];

  // Module state
  var modal = null;
  var inputEl = null;
  var listEl = null;
  var items = [];     // Currently-rendered filtered items
  var activeIdx = 0;
  var dynamicApps = null;   // null = not loaded yet; [] = loaded, empty
  var open = false;

  // ── Fuzzy filter ────────────────────────────────────────────────────────
  function fuzzyScore(query, label) {
    // Lower-cased subsequence match. Returns true/false; rank by length
    // diff so shorter labels win ties.
    if (!query) return true;
    var q = query.toLowerCase();
    var s = label.toLowerCase();
    if (s.indexOf(q) !== -1) return true;  // Substring is a strong match
    var qi = 0;
    for (var i = 0; i < s.length && qi < q.length; i++) {
      if (s[i] === q[qi]) qi++;
    }
    return qi === q.length;
  }

  // ── Dynamic apps ────────────────────────────────────────────────────────
  function scrapeAppsFromDom() {
    var nodes = document.querySelectorAll("[data-app-name]");
    var seen = {};
    var out = [];
    nodes.forEach(function (n) {
      var name = (n.getAttribute("data-app-name") || "").trim();
      if (!name || seen[name]) return;
      seen[name] = true;
      out.push({ label: name, url: "/app/" + encodeURIComponent(name) + "/production", hint: "App" });
    });
    if (out.length === 0) {
      // Fallback: scrape .app-tile-name anchors (dashboard.html structure)
      document.querySelectorAll(".app-tile-name").forEach(function (a) {
        var name = (a.textContent || "").trim();
        var href = a.getAttribute("href") || "";
        if (!name || seen[name]) return;
        seen[name] = true;
        out.push({ label: name, url: href, hint: "App" });
      });
    }
    return out;
  }

  // Exposed for tests + future per-env palettes. The default environment
  // matches the server-side default in web/routes/api.py::list_apps.
  var APPS_ENDPOINT = "/api/apps";
  var DEFAULT_ENV = "production";

  function loadDynamicApps() {
    if (dynamicApps !== null) return Promise.resolve(dynamicApps);
    return fetch(APPS_ENDPOINT, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        var out = [];
        if (Array.isArray(data)) {
          var seen = {};
          data.forEach(function (row) {
            // The endpoint keys app names under .app; .name is tolerated
            // for forward-compat with a future shape switch.
            var name = row && (row.app || row.name);
            if (!name || seen[name]) return;
            seen[name] = true;
            // The server query'd a single environment (default
            // production); .environment is not in the response, so fall
            // through to the same default the server used.
            var env = row.environment || DEFAULT_ENV;
            out.push({
              label: name,
              url: "/app/" + encodeURIComponent(name) + "/" + encodeURIComponent(env),
              hint: "App",
            });
          });
        }
        // Fresh installs return [] (no app_state rows yet). DOM-scrape
        // ensures the palette is still useful before the first run.
        if (out.length === 0) out = scrapeAppsFromDom();
        dynamicApps = out;
        return out;
      })
      .catch(function () {
        // Network/auth failure — degrade gracefully to DOM scrape.
        dynamicApps = scrapeAppsFromDom();
        return dynamicApps;
      });
  }

  // ── Render ──────────────────────────────────────────────────────────────
  function build() {
    var mount = document.getElementById("cmdk-mount");
    if (!mount) return null;
    if (mount.querySelector(".cmdk-modal")) return mount.querySelector(".cmdk-modal");

    var wrapper = document.createElement("div");
    wrapper.className = "cmdk-modal";
    wrapper.setAttribute("role", "dialog");
    wrapper.setAttribute("aria-label", "Command palette");
    wrapper.hidden = true;

    var backdrop = document.createElement("div");
    backdrop.className = "cmdk-backdrop";
    backdrop.addEventListener("click", close);
    wrapper.appendChild(backdrop);

    var panel = document.createElement("div");
    panel.className = "cmdk-panel";

    var input = document.createElement("input");
    input.type = "text";
    input.className = "cmdk-input";
    input.placeholder = "Type a command or page name…";
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("aria-label", "Command palette search");
    input.addEventListener("input", function () { render(input.value); });
    input.addEventListener("keydown", onInputKey);
    panel.appendChild(input);

    var list = document.createElement("ul");
    list.className = "cmdk-list";
    list.setAttribute("role", "listbox");
    panel.appendChild(list);

    wrapper.appendChild(panel);
    mount.appendChild(wrapper);

    modal = wrapper;
    inputEl = input;
    listEl = list;
    return wrapper;
  }

  function clearChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function allCandidates() {
    var all = STATIC_ROUTES.slice();
    if (dynamicApps && dynamicApps.length) {
      all = all.concat(dynamicApps);
    }
    return all;
  }

  function render(query) {
    if (!listEl) return;
    var all = allCandidates();
    var filtered = all.filter(function (it) { return fuzzyScore(query || "", it.label); });
    // Stable rank: substring matches first, then by label length.
    var q = (query || "").toLowerCase();
    filtered.sort(function (a, b) {
      var aSub = a.label.toLowerCase().indexOf(q) !== -1 ? 0 : 1;
      var bSub = b.label.toLowerCase().indexOf(q) !== -1 ? 0 : 1;
      if (aSub !== bSub) return aSub - bSub;
      return a.label.length - b.label.length;
    });
    items = filtered;
    if (activeIdx >= items.length) activeIdx = Math.max(0, items.length - 1);
    if (activeIdx < 0) activeIdx = 0;

    // DOM
    clearChildren(listEl);
    if (items.length === 0) {
      var empty = document.createElement("li");
      empty.className = "cmdk-empty";
      empty.textContent = "No matches.";
      listEl.appendChild(empty);
      return;
    }
    items.forEach(function (it, i) {
      var li = document.createElement("li");
      li.className = "cmdk-result" + (i === activeIdx ? " active" : "");
      li.setAttribute("role", "option");
      li.setAttribute("data-idx", String(i));
      var label = document.createElement("span");
      label.className = "cmdk-result-label";
      label.textContent = it.label;
      li.appendChild(label);
      if (it.hint) {
        var hint = document.createElement("span");
        hint.className = "cmdk-result-hint";
        hint.textContent = it.hint;
        li.appendChild(hint);
      }
      li.addEventListener("mouseenter", function () { setActive(i); });
      li.addEventListener("click", function () { setActive(i); selectActive(); });
      listEl.appendChild(li);
    });
  }

  function setActive(i) {
    if (i < 0 || i >= items.length) return;
    activeIdx = i;
    var lis = listEl.querySelectorAll(".cmdk-result");
    lis.forEach(function (li, idx) {
      li.classList.toggle("active", idx === activeIdx);
    });
    var el = lis[activeIdx];
    if (el && el.scrollIntoView) {
      el.scrollIntoView({ block: "nearest" });
    }
  }

  function selectActive() {
    var it = items[activeIdx];
    if (!it) return;
    close();
    window.location.href = it.url;
  }

  function onInputKey(e) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(Math.min(activeIdx + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(Math.max(activeIdx - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      selectActive();
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  }

  // ── Open / close ────────────────────────────────────────────────────────
  function openModal() {
    if (open) return;
    var m = build();
    if (!m) return;
    open = true;
    activeIdx = 0;
    inputEl.value = "";
    m.hidden = false;
    loadDynamicApps().then(function () { render(""); });
    render("");
    setTimeout(function () { try { inputEl.focus(); } catch (e) {} }, 0);
  }

  function close() {
    if (!open) return false;
    open = false;
    if (modal) modal.hidden = true;
    return true;
  }

  function onGlobalKey(e) {
    var isCmdK = (e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey);
    if (isCmdK) {
      e.preventDefault();
      if (open) close(); else openModal();
    }
  }

  function init() {
    build();
    window.addEventListener("keydown", onGlobalKey);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.cmdk = {
    open: openModal,
    close: close,
    isOpen: function () { return open; },
  };
})();
