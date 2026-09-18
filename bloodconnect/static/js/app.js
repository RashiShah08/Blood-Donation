// Shared helpers used by every page: navigation, toasts, API calls and map setup.
(() => {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content ?? "";

  // Mobile navigation
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!open));
      nav.classList.toggle("is-open", !open);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && nav.classList.contains("is-open")) {
        toggle.setAttribute("aria-expanded", "false");
        nav.classList.remove("is-open");
        toggle.focus();
      }
    });
  }

  // Shown inside another site's frame (such as a portfolio preview), only the public pages can load there.
  // Account pages and other sites refuse to be framed, so links to them open in a new tab instead.
  if (window.self !== window.top) {
    const staysInFrame = (link) =>
      link.origin === window.location.origin && !/^\/(donor|hospital)(\/|$)/.test(link.pathname);
    document.addEventListener("click", (event) => {
      const link = event.target.closest?.("a[href]");
      if (!link || !/^https?:$/.test(link.protocol) || staysInFrame(link)) return;
      link.target = "_blank";
      link.rel = "noopener";
    }, true);
  }

  const TOAST_ICONS = { success: "check", error: "alert", info: "info" };

  function icon(name) {
    const base = document.getElementById("toast-region")?.dataset.icons ?? "";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `${base}#${name}`);
    svg.append(use);
    return svg;
  }

  // Toasts slide in, show a countdown bar that pauses while hovered, and can be dismissed.
  function toast(message, type = "info", timeout = 6000) {
    const region = document.getElementById("toast-region");
    if (!region) return;
    const item = document.createElement("div");
    item.className = `toast toast-${type}`;
    item.setAttribute("role", type === "error" ? "alert" : "status");
    item.style.setProperty("--toast-duration", `${timeout}ms`);
    const text = document.createElement("p");
    text.className = "toast-text";
    text.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.append(icon("plus"));
    const timer = document.createElement("span");
    timer.className = "toast-timer";
    timer.setAttribute("aria-hidden", "true");
    item.append(icon(TOAST_ICONS[type] ?? "info"), text, close, timer);

    let remaining = timeout;
    let startedAt = Date.now();
    let handle = window.setTimeout(dismiss, remaining);
    function dismiss() {
      window.clearTimeout(handle);
      if (item.classList.contains("is-leaving")) return;
      item.classList.add("is-leaving");
      window.setTimeout(() => item.remove(), reduceMotion ? 0 : 220);
    }
    item.addEventListener("mouseenter", () => {
      window.clearTimeout(handle);
      remaining -= Date.now() - startedAt;
    });
    item.addEventListener("mouseleave", () => {
      startedAt = Date.now();
      handle = window.setTimeout(dismiss, Math.max(remaining, 1200));
    });
    close.addEventListener("click", dismiss);
    region.appendChild(item);
  }

  // Following an in-page link must move keyboard focus, which Safari does not do on its own.
  document.addEventListener("click", (event) => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.defaultPrevented) return;
    const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
    if (!target) return;
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    requestAnimationFrame(() => target.focus({ preventScroll: true }));
  });

  // A thin progress bar at the top of the page while the next page loads.
  const navProgress = document.createElement("div");
  navProgress.className = "nav-progress";
  navProgress.setAttribute("aria-hidden", "true");
  document.body.append(navProgress);
  function startNavigation() {
    navProgress.classList.remove("is-done");
    void navProgress.offsetWidth;
    navProgress.classList.add("is-loading");
  }
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== "_self") return;
    if (link.hasAttribute("download")) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) return;
    startNavigation();
  });
  document.addEventListener("submit", (event) => {
    window.setTimeout(() => {
      if (!event.defaultPrevented && !event.target.matches("[data-availability]")) startNavigation();
    }, 0);
  });
  window.addEventListener("pageshow", () => {
    navProgress.classList.remove("is-loading");
    navProgress.classList.add("is-done");
  });

  async function api(url, { method = "GET", body } = {}) {
    const headers = { Accept: "application/json", "X-CSRFToken": csrfToken };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        credentials: "same-origin",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new Error("You appear to be offline. Check your connection and try again.");
    }
    let data = {};
    try {
      data = await response.json();
    } catch {
      /* non-JSON response */
    }
    if (!response.ok) {
      if (response.status === 401) {
        window.location.reload();
      }
      const error = new Error(data.error || `Something went wrong (${response.status}).`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function distanceKm(lat1, lng1, lat2, lng2) {
    const toRad = (deg) => (deg * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLng = toRad(lng2 - lng1);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
    return 2 * 6371.0088 * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  function formatDistance(km) {
    return km >= 1 ? `${km.toFixed(1)} km` : `${Math.round(km * 1000)} m`;
  }

  function createMap(element, center, zoom) {
    if (!window.L) {
      element.textContent = "The map couldn't be loaded. Please refresh the page.";
      return null;
    }
    const map = window.L.map(element, { scrollWheelZoom: false }).setView(center, zoom);
    window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    // Enable scroll zoom only after the user interacts, so the page scrolls normally.
    map.once("focus click", () => map.scrollWheelZoom.enable());
    // Maps stretch with their layout (for example when a donor list loads beside them); keep tiles filled.
    if ("ResizeObserver" in window) new ResizeObserver(() => map.invalidateSize()).observe(element);
    return map;
  }

  // Confirmation prompts for destructive forms
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form.matches("form[data-confirm]") && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
    }
  });

  // Prevent double submission (disable after the browser has collected the form data)
  document.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    const buttons = event.target.querySelectorAll('button[type="submit"]');
    window.setTimeout(() => buttons.forEach((button) => {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }), 0);
  });

  // Restore buttons when the page is shown from the back/forward cache
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    document.querySelectorAll('button[aria-busy="true"]').forEach((button) => {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
  });

  document.querySelectorAll("[data-print]").forEach((button) => button.addEventListener("click", () => window.print()));
  document.querySelector("[data-autofocus]")?.focus();

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function relativeTime(iso) {
    const seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
    for (const [unit, size] of [["day", 86400], ["hour", 3600], ["minute", 60]]) {
      if (seconds >= size) {
        const count = Math.floor(seconds / size);
        return `${count} ${unit}${count === 1 ? "" : "s"} ago`;
      }
    }
    return "just now";
  }

  // Numbers in KPI tiles count up on load ("12" or "10 min"; values like "2 / 3" are left alone).
  function countUp(element) {
    const match = element.textContent.trim().match(/^(\d+)(\D*)$/);
    if (!match || reduceMotion) return;
    const target = Number(match[1]);
    const suffix = match[2];
    if (target === 0) return;
    // Live updates compare against the final value, not the half-animated one.
    element.dataset.value = match[0];
    const duration = Math.min(900, 350 + target * 40);
    const start = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - start) / duration);
      element.textContent = `${Math.round(target * (1 - (1 - progress) ** 3))}${suffix}`;
      if (element.dataset.value !== match[0]) return;
      if (progress < 1) requestAnimationFrame(step);
    };
    element.textContent = `0${suffix}`;
    requestAnimationFrame(step);
  }
  document.querySelectorAll(".kpi-value").forEach(countUp);

  // Replace a live number and flash it so the change is noticed.
  function setNumber(element, value) {
    const text = String(value);
    const current = element.dataset.value ?? element.textContent.trim();
    if (current === text) return;
    element.dataset.value = text;
    element.textContent = text;
    element.classList.remove("is-updated");
    void element.offsetWidth;
    element.classList.add("is-updated");
  }

  // Desktop notification, only when the user opted in and the tab is in the background.
  function notify(title, body, url) {
    if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
    const notification = new Notification(title, { body, tag: url || title });
    notification.onclick = () => {
      window.focus();
      if (url) window.location.href = url;
      notification.close();
    };
  }

  // "On this page" navigation highlights the section being read.
  const toc = document.querySelector("[data-toc]");
  if (toc && "IntersectionObserver" in window) {
    const links = [...toc.querySelectorAll('a[href^="#"]')];
    const targets = links.map((link) => document.getElementById(decodeURIComponent(link.hash.slice(1)))).filter(Boolean);
    const setActive = (id) => links.forEach((link) => link.setAttribute("aria-current", String(link.hash === `#${id}`)));
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-90px 0px -55% 0px" },
    );
    targets.forEach((target) => observer.observe(target));
    if (targets[0]) setActive(targets[0].id);
  }

  // FAQ search filters questions as you type.
  const faq = document.querySelector("[data-faq]");
  if (faq) {
    const input = faq.querySelector("[data-faq-search]");
    const items = [...faq.querySelectorAll("details")];
    const headings = [...faq.querySelectorAll("h2")];
    const empty = faq.querySelector("[data-faq-empty]");
    input.addEventListener("input", () => {
      const query = input.value.trim().toLowerCase();
      let shown = 0;
      items.forEach((item) => {
        const match = !query || item.textContent.toLowerCase().includes(query);
        item.hidden = !match;
        item.open = Boolean(query) && match;
        if (match) shown += 1;
      });
      headings.forEach((heading) => {
        let node = heading.nextElementSibling;
        let visible = false;
        while (node && node.tagName !== "H2") {
          if (node.tagName === "DETAILS" && !node.hidden) visible = true;
          node = node.nextElementSibling;
        }
        heading.hidden = !visible;
      });
      empty.hidden = shown > 0;
    });
  }

  // Tiny DOM builder shared by the live pages (text is always set as text, never HTML).
  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  window.BloodConnect = { api, toast, createMap, distanceKm, formatDistance, relativeTime, countUp, setNumber, notify, h, reduceMotion };
})();
