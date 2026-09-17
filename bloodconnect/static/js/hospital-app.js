// Hospital app shell: Ctrl/Cmd+K command palette and live dashboard updates with activity toasts.
(() => {
  "use strict";

  const { api, toast, notify, setNumber, relativeTime, h } = window.BloodConnect;
  const SVG_NS = "http://www.w3.org/2000/svg";

  // ---------- Command palette ----------
  const dialog = document.querySelector("[data-palette]");
  if (dialog && typeof dialog.showModal === "function") {
    const input = dialog.querySelector("input");
    const list = dialog.querySelector(".palette-list");
    const commands = JSON.parse(dialog.dataset.commands);
    let requestItems = [];
    let results = [];
    let active = 0;

    function icon(name) {
      const svg = document.createElementNS(SVG_NS, "svg");
      svg.setAttribute("class", "icon");
      svg.setAttribute("aria-hidden", "true");
      const use = document.createElementNS(SVG_NS, "use");
      use.setAttribute("href", `${dialog.dataset.sprite}#${name}`);
      svg.append(use);
      return svg;
    }

    async function loadRequests() {
      try {
        const data = await api(dialog.dataset.opsUrl);
        requestItems = data.requests.map((request) => ({
          group: "Open requests",
          label: `Request #${request.id} · ${request.blood_group} · ${request.patient_name}`,
          url: request.detail_url,
          icon: "drop",
          keywords: `${request.urgency} ${request.blood_group} ${request.id}`,
          meta: `${request.units_pledged}/${request.units_required} pledged`,
        }));
        if (dialog.open) render();
      } catch {
        /* the palette still works for pages and actions */
      }
    }

    function allItems() {
      return [
        ...commands.filter((item) => item.group !== "Help"),
        ...requestItems,
        ...commands.filter((item) => item.group === "Help"),
      ];
    }

    function render() {
      const query = input.value.trim().toLowerCase();
      const terms = query.split(/\s+/).filter(Boolean);
      results = allItems().filter((item) => {
        const haystack = `${item.label} ${item.keywords || ""} ${item.group}`.toLowerCase();
        return terms.every((term) => haystack.includes(term));
      });
      active = Math.min(active, Math.max(0, results.length - 1));
      list.replaceChildren();
      if (!results.length) {
        list.append(h("li", { class: "palette-empty", role: "presentation" }, `No results for "${input.value.trim()}"`));
        input.removeAttribute("aria-activedescendant");
        return;
      }
      let lastGroup = null;
      results.forEach((item, index) => {
        if (item.group !== lastGroup) {
          list.append(h("li", { class: "palette-group", role: "presentation" }, item.group));
          lastGroup = item.group;
        }
        const option = h(
          "li",
          { class: "palette-item", role: "option", id: `palette-option-${index}`, "aria-selected": String(index === active) },
          icon(item.icon),
          h("span", {}, item.label),
          item.meta ? h("span", { class: "palette-item-meta" }, item.meta) : null,
        );
        option.addEventListener("mousemove", () => {
          if (active !== index) {
            active = index;
            highlight();
          }
        });
        option.addEventListener("click", () => go(item));
        list.append(option);
      });
      highlight();
    }

    function highlight() {
      list.querySelectorAll(".palette-item").forEach((option) => {
        option.setAttribute("aria-selected", String(option.id === `palette-option-${active}`));
      });
      const selected = document.getElementById(`palette-option-${active}`);
      if (selected) {
        input.setAttribute("aria-activedescendant", selected.id);
        selected.scrollIntoView({ block: "nearest" });
      }
    }

    function go(item) {
      dialog.close();
      window.location.href = item.url;
    }

    function open() {
      if (dialog.open) return;
      input.value = "";
      active = 0;
      render();
      dialog.showModal();
      input.focus();
      loadRequests();
    }

    input.addEventListener("input", () => {
      active = 0;
      render();
    });
    input.addEventListener("keydown", (event) => {
      if (!results.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        active = (active + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
        highlight();
      } else if (event.key === "Enter") {
        event.preventDefault();
        go(results[active]);
      }
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    document.querySelectorAll("[data-palette-open]").forEach((button) => button.addEventListener("click", open));
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (dialog.open) dialog.close();
        else open();
      } else if (event.key === "/" && !dialog.open && !event.target.closest("input, textarea, select, [contenteditable]")) {
        event.preventDefault();
        open();
      }
    });
    dialog.dataset.ready = "true";
  }

  // ---------- Live updates ----------
  const live = document.querySelector("[data-hospital-live]");
  if (!live) return;

  const REFRESH_MS = 15000;
  const TOAST_KINDS = new Set(["pledged", "arrived", "cancelled", "no_show"]);
  const activityList = document.querySelector("[data-activity-list]");
  const activityEmpty = document.querySelector("[data-activity-empty]");
  const livePills = document.querySelectorAll("[data-live-state]");
  const eventKey = (event) => `${event.kind}|${event.at}|${event.text}`;
  let seen = null;

  function renderActivity(events) {
    const firstRender = seen === null;
    activityList.replaceChildren(
      ...events.slice(0, 10).map((event) => {
        const item = h(
          "li",
          { dataset: { key: eventKey(event) } },
          h("span", { class: "activity-dot", dataset: { kind: event.kind }, "aria-hidden": "true" }),
          h("span", {}, event.text, h("span", { class: "activity-time" }, relativeTime(event.at))),
        );
        if (!firstRender && !seen.has(eventKey(event))) item.classList.add("is-new");
        return item;
      }),
    );
    if (activityEmpty) activityEmpty.hidden = events.length > 0;
  }

  async function refresh() {
    try {
      const data = await api(live.dataset.opsUrl);
      livePills.forEach((pill) => {
        pill.dataset.state = "live";
        pill.textContent = "Live";
      });
      const kpis = {
        open_requests: data.summary.open_requests,
        units_needed: data.summary.units_needed,
        active_pledges: data.summary.on_the_way + data.summary.arrived,
      };
      for (const [key, value] of Object.entries(kpis)) {
        const element = document.querySelector(`[data-kpi="${key}"] .kpi-value`);
        if (element) setNumber(element, value);
      }
      if (activityList) renderActivity(data.activity);
      if (seen) {
        data.activity
          .filter((event) => !seen.has(eventKey(event)) && TOAST_KINDS.has(event.kind))
          .slice(0, 3)
          .reverse()
          .forEach((event) => {
            toast(event.text, event.kind === "cancelled" || event.kind === "no_show" ? "error" : "success");
            notify("BloodConnect", event.text);
          });
      }
      seen = new Set(data.activity.map(eventKey));
      live.dataset.liveReady = "true";
    } catch {
      livePills.forEach((pill) => {
        pill.dataset.state = "stale";
        pill.textContent = "Reconnecting";
      });
    }
  }

  refresh();
  let timer = window.setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    window.clearInterval(timer);
    if (!document.hidden) {
      refresh();
      timer = window.setInterval(refresh, REFRESH_MS);
    }
  });
})();
