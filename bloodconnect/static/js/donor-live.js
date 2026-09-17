// Donor dashboard: live request feed, one-tap availability switch and opt-in desktop alerts.
(() => {
  "use strict";

  const root = document.querySelector("[data-live-feed]");
  if (!root) return;

  const { api, toast, notify, relativeTime, formatDistance, h } = window.BloodConnect;
  const REFRESH_MS = 15000;
  const URGENCY = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };

  const list = root.querySelector("[data-request-list]");
  const emptyUnavailable = root.querySelector('[data-empty="unavailable"]');
  const emptyNone = root.querySelector('[data-empty="none"]');
  const intro = root.querySelector("[data-list-intro]");
  const liveState = root.querySelector("[data-live-state]");
  const banner = document.querySelector("[data-pledge-banner]");
  const alertsButton = root.querySelector("[data-desktop-alerts]");
  const alertsLabel = root.querySelector("[data-desktop-alerts-label]");

  const knownIds = new Set([...list.querySelectorAll("[data-request-id]")].map((item) => Number(item.dataset.requestId)));
  const pledgeSignature = (ids, statuses) => ids.map((id, index) => `${id}:${statuses[index]}`).join(",");
  const initialPledges = pledgeSignature(JSON.parse(root.dataset.pledges), JSON.parse(root.dataset.pledgeStatuses));

  const signature = (request) => `${request.units_remaining}|${request.urgency}`;

  function card(request) {
    return h(
      "li",
      { class: "request-card", dataset: { requestId: String(request.id), signature: signature(request) } },
      h("span", { class: "blood-badge" }, h("span", { class: "visually-hidden" }, "Blood group "), request.blood_group),
      h(
        "div",
        {},
        h(
          "div",
          { class: "request-head" },
          h("h3", {}, request.hospital),
          h("span", { class: `badge badge-urgency-${request.urgency}` }, `${URGENCY[request.urgency] || request.urgency} urgency`),
        ),
        h("p", { class: "request-meta" }, `${request.city} · ${formatDistance(request.distance_km)} away · posted ${relativeTime(request.posted_at)}`),
        h(
          "p",
          { class: "request-units" },
          h("strong", {}, String(request.units_remaining)),
          ` ${request.units_remaining === 1 ? "unit" : "units"} still needed`,
        ),
      ),
      h("div", { class: "request-actions" }, h("a", { class: "btn btn-primary", href: request.url }, "I can help")),
    );
  }

  function renderRequests(requests) {
    const currentIds = [...list.children].map((item) => Number(item.dataset.requestId));
    const desiredIds = requests.map((request) => request.id);
    const sameOrder = currentIds.length === desiredIds.length && currentIds.every((id, index) => id === desiredIds[index]);

    if (sameOrder) {
      // Keep existing nodes (and keyboard focus); only swap cards whose details changed.
      requests.forEach((request) => {
        const item = list.querySelector(`[data-request-id="${request.id}"]`);
        if (item && item.dataset.signature !== signature(request)) item.replaceWith(card(request));
      });
      return [];
    }

    const fresh = [];
    const nodes = requests.map((request) => {
      const existing = list.querySelector(`[data-request-id="${request.id}"]`);
      if (existing && existing.dataset.signature === signature(request)) return existing;
      const node = card(request);
      if (!knownIds.has(request.id)) {
        node.classList.add("is-new");
        fresh.push(request);
      }
      return node;
    });
    list.replaceChildren(...nodes);
    return fresh;
  }

  function render(data) {
    setSwitch(data.available);
    emptyUnavailable.hidden = data.available;
    emptyNone.hidden = !data.available || data.requests.length > 0;
    intro.hidden = data.requests.length === 0;

    const fresh = renderRequests(data.requests);
    if (fresh.length) {
      const first = fresh[0];
      const extra = fresh.length > 1 ? ` (+${fresh.length - 1} more)` : "";
      toast(`New request near you: ${first.hospital} needs ${first.blood_group} blood${extra}.`, "success");
      notify("A hospital near you needs blood", `${first.hospital} needs ${first.blood_group} (${URGENCY[first.urgency]} urgency).`, first.url);
    }
    data.requests.forEach((request) => knownIds.add(request.id));

    const pledges = pledgeSignature(
      data.pledges.map((pledge) => pledge.id),
      data.pledges.map((pledge) => pledge.status),
    );
    if (banner) banner.hidden = pledges === initialPledges;
  }

  async function refresh() {
    try {
      const data = await api(root.dataset.feedUrl);
      liveState.dataset.state = "live";
      liveState.textContent = "Live";
      render(data);
    } catch {
      liveState.dataset.state = "stale";
      liveState.textContent = "Reconnecting";
    }
  }

  // Availability switch: works as a normal form without JavaScript, instantly with it.
  const availabilityForm = document.querySelector("[data-availability]");
  const toggle = availabilityForm?.querySelector(".switch");
  const toggleLabel = availabilityForm?.querySelector("[data-switch-label]");

  function setSwitch(on) {
    if (!toggle) return;
    toggle.setAttribute("aria-checked", String(on));
    toggleLabel.textContent = on ? "Available for requests" : "Not available";
    availabilityForm.querySelector('input[name="available"]').value = on ? "no" : "yes";
  }

  availabilityForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const next = toggle.getAttribute("aria-checked") !== "true";
    toggle.setAttribute("aria-busy", "true");
    setSwitch(next);
    try {
      const data = await api(availabilityForm.dataset.apiUrl, { method: "POST", body: { available: next } });
      setSwitch(data.available);
      toast(
        data.available ? "You're available. Matching requests will appear here." : "Alerts paused. Switch back on whenever you're ready.",
        "success",
      );
      await refresh();
    } catch (error) {
      setSwitch(!next);
      toast(error.message, "error");
    } finally {
      toggle.removeAttribute("aria-busy");
    }
  });

  document.querySelector("[data-reload]")?.addEventListener("click", () => window.location.reload());

  // Desktop alerts (opt-in).
  function updateAlertsButton() {
    if (!("Notification" in window) || Notification.permission === "denied") {
      alertsButton.hidden = true;
      return;
    }
    alertsButton.hidden = false;
    const granted = Notification.permission === "granted";
    alertsLabel.textContent = granted ? "Desktop alerts on" : "Turn on desktop alerts";
    alertsButton.disabled = granted;
  }
  alertsButton?.addEventListener("click", async () => {
    await Notification.requestPermission();
    updateAlertsButton();
    if (Notification.permission === "granted") toast("Desktop alerts are on. We'll notify you when this tab is in the background.", "success");
  });
  if (alertsButton) updateAlertsButton();

  root.dataset.ready = "true";
  let timer = window.setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    window.clearInterval(timer);
    if (!document.hidden) {
      refresh();
      timer = window.setInterval(refresh, REFRESH_MS);
    }
  });
})();
