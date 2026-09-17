// Live operations console: polls the hospital snapshot, shows the request queue, donors on the
// way with ticking ETAs, a map of available donors and an activity feed. Keyboard-driven.
(() => {
  "use strict";

  const root = document.getElementById("ops");
  if (!root) return;

  const { api, toast, createMap, notify, setNumber, h } = window.BloodConnect;
  const REFRESH_MS = 10000;
  const DONOR_REFRESH_MS = 30000;
  const URGENCY_LABEL = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
  const STATUS_COLOR = { available: "#7fb2ff", alerted: "#f5b454", pledged: "#5fd49a" };
  const ALERT_KINDS = new Set(["pledged", "arrived"]);

  const els = {
    live: root.querySelector("[data-live]"),
    updated: root.querySelector("[data-updated]"),
    queue: root.querySelector("[data-queue]"),
    queueEmpty: root.querySelector("[data-queue-empty]"),
    detail: root.querySelector("[data-detail]"),
    detailLink: root.querySelector("[data-detail-link]"),
    feed: root.querySelector("[data-feed]"),
    radius: root.querySelector("[data-radius]"),
    notify: root.querySelector("[data-notify]"),
    notifyLabel: root.querySelector("[data-notify-label]"),
    sound: root.querySelector("[data-sound]"),
    fullscreen: root.querySelector("[data-fullscreen]"),
    shortcutsOpen: root.querySelector("[data-shortcuts-open]"),
    shortcuts: document.querySelector("[data-shortcuts]"),
  };

  const hospital = {
    lat: parseFloat(root.dataset.hospitalLat),
    lng: parseFloat(root.dataset.hospitalLng),
    name: root.dataset.hospitalName,
  };

  const map = createMap(root.querySelector("[data-map]"), [hospital.lat, hospital.lng], 13);
  if (map) {
    const icon = window.L.divIcon({ className: "ops-hospital-marker", iconSize: [16, 16] });
    window.L.marker([hospital.lat, hospital.lng], { icon, title: hospital.name }).addTo(map).bindTooltip(hospital.name);
  }
  const donorLayer = map ? window.L.layerGroup().addTo(map) : null;
  let radiusCircle = null;

  let snapshot = null;
  let selectedId = Number((window.location.hash.match(/^#request-(\d+)$/) || [])[1]) || null;
  let donorData = null;
  let lastDonorFetch = 0;
  let seenEvents = null;
  let seenPledges = null;

  // ---------- Sound ----------
  let audio = null;
  let soundOn = false;
  try {
    soundOn = localStorage.getItem("bloodconnect-ops-sound") === "on";
  } catch {
    /* storage unavailable */
  }

  function renderSound() {
    els.sound.setAttribute("aria-pressed", String(soundOn));
    els.sound.querySelector('[data-sound-icon="on"]').hidden = !soundOn;
    els.sound.querySelector('[data-sound-icon="off"]').hidden = soundOn;
  }

  function toggleSound() {
    soundOn = !soundOn;
    try {
      localStorage.setItem("bloodconnect-ops-sound", soundOn ? "on" : "off");
    } catch {
      /* storage unavailable */
    }
    renderSound();
    if (soundOn) ping();
    toast(soundOn ? "Sound alerts on." : "Sound alerts off.", "info", 2500);
  }

  function ping() {
    if (!soundOn || !window.AudioContext) return;
    audio = audio || new AudioContext();
    const now = audio.currentTime;
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(880, now);
    oscillator.frequency.exponentialRampToValueAtTime(1320, now + 0.12);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.18, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start(now);
    oscillator.stop(now + 0.4);
  }

  // ---------- Formatting ----------
  function minutesSince(iso) {
    return Math.max(0, (Date.now() - Date.parse(iso)) / 60000);
  }

  function ago(iso) {
    const minutes = Math.round(minutesSince(iso));
    if (minutes < 1) return "now";
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    return hours < 24 ? `${hours}h` : `${Math.floor(hours / 24)}d`;
  }

  function clock(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  // The ETA keeps counting down between updates from the donor's phone.
  function liveEta(pledge) {
    if (pledge.eta_minutes === null) return null;
    const elapsed = pledge.progress_updated_at ? minutesSince(pledge.progress_updated_at) : 0;
    return Math.max(0, Math.round(pledge.eta_minutes - elapsed));
  }

  function setLive(ok, message) {
    els.live.dataset.state = ok ? "live" : "stale";
    els.live.textContent = ok ? "LIVE" : "RECONNECTING";
    if (!ok && message) els.live.title = message;
  }

  function selectedRequest() {
    return snapshot?.requests.find((item) => item.id === selectedId) || null;
  }

  // ---------- Rendering ----------
  function renderSummary() {
    for (const [key, value] of Object.entries(snapshot.summary)) {
      const node = root.querySelector(`[data-summary-key="${key}"]`);
      if (node) setNumber(node, value);
    }
  }

  function renderQueue() {
    els.queue.replaceChildren();
    els.queueEmpty.hidden = snapshot.requests.length > 0;
    for (const item of snapshot.requests) {
      const onTheWay = item.pledges.filter((p) => p.status !== "arrived").length;
      const arrived = item.pledges.length - onTheWay;
      const button = h(
        "button",
        { type: "button", class: "queue-item", "aria-pressed": String(item.id === selectedId), dataset: { urgency: item.urgency, requestId: String(item.id) } },
        h(
          "span",
          { class: "queue-top" },
          h("span", {}, h("span", { class: "queue-group" }, item.blood_group), ` · #${item.id}`),
          h("span", { class: `tag tag-${item.urgency}` }, URGENCY_LABEL[item.urgency] || item.urgency),
        ),
        h("span", { class: "meter", "aria-hidden": "true" }, h("span", { style: `width:${Math.round((item.units_pledged / item.units_required) * 100)}%` })),
        h(
          "span",
          { class: "queue-meta" },
          h("span", {}, `${item.units_pledged}/${item.units_required} pledged`),
          h("span", {}, `${onTheWay} en route · ${arrived} here · ${ago(item.created_at)}`),
        ),
      );
      button.addEventListener("click", () => select(item.id));
      els.queue.append(h("li", {}, button));
    }
  }

  function etaCell(pledge) {
    if (pledge.status === "arrived") return h("span", { class: "eta-value", dataset: { tone: "ok" } }, "ARRIVED");
    const eta = liveEta(pledge);
    if (eta === null) return h("span", { class: "eta-value", dataset: { tone: "idle" } }, "No live ETA");
    return h("span", { class: "eta-value", dataset: { tone: eta <= 10 ? "ok" : "warn" } }, `${eta} min`);
  }

  function renderDetail() {
    if (!snapshot) return;
    const item = selectedRequest();
    els.detail.replaceChildren();
    if (!item) {
      els.detailLink.hidden = true;
      els.detail.append(h("p", { class: "ops-empty" }, snapshot.requests.length ? "Select a request to see donors on the way." : "Nothing to show yet."));
      return;
    }
    els.detailLink.hidden = false;
    els.detailLink.href = item.detail_url;

    const pledgeList = item.pledges.length
      ? h(
          "ul",
          { class: "eta-list" },
          item.pledges.map((pledge) => {
            const row = h(
              "li",
              { class: "eta-item" },
              h("span", { class: "eta-name" }, pledge.donor_name),
              etaCell(pledge),
              h(
                "span",
                { class: "eta-sub" },
                [pledge.blood_group, pledge.distance_km !== null && pledge.status !== "arrived" ? `${pledge.distance_km} km left` : null, pledge.phone]
                  .filter(Boolean)
                  .join(" · "),
              ),
            );
            if (seenPledges && !seenPledges.has(`${pledge.id}:${pledge.status}`)) row.classList.add("is-new");
            return row;
          }),
        )
      : h("p", { class: "ops-empty" }, "No donors have pledged yet.");

    els.detail.append(
      h("h3", {}, `#${item.id} · ${item.blood_group} · ${item.patient_name}`),
      h(
        "dl",
        { class: "ops-kv" },
        h("div", {}, h("dt", {}, "Needed"), h("dd", {}, String(item.units_remaining))),
        h("div", {}, h("dt", {}, "Pledged"), h("dd", {}, `${item.units_pledged}/${item.units_required}`)),
        h("div", {}, h("dt", {}, "Alerted"), h("dd", {}, String(item.notified))),
      ),
      h("h3", {}, "Donors"),
      pledgeList,
    );
  }

  const eventKey = (event) => `${event.kind}|${event.at}|${event.text}`;

  function renderFeed() {
    els.feed.replaceChildren(
      ...snapshot.activity.map((event) => {
        const row = h("li", {}, h("time", { datetime: event.at }, clock(event.at)), h("span", { dataset: { kind: event.kind } }, event.text));
        if (seenEvents && !seenEvents.has(eventKey(event))) row.classList.add("is-new");
        return row;
      }),
    );
    if (!snapshot.activity.length) els.feed.append(h("li", {}, h("span", {}, ""), h("span", { class: "muted" }, "No activity yet.")));
  }

  function announceNewEvents() {
    if (!seenEvents) return;
    const fresh = snapshot.activity.filter((event) => !seenEvents.has(eventKey(event)) && ALERT_KINDS.has(event.kind));
    if (!fresh.length) return;
    ping();
    fresh
      .slice(0, 3)
      .reverse()
      .forEach((event) => {
        toast(event.text, "success");
        notify("BloodConnect live ops", event.text);
      });
  }

  function renderDonors() {
    if (!map) return;
    donorLayer.clearLayers();
    if (radiusCircle) {
      radiusCircle.remove();
      radiusCircle = null;
    }
    const item = selectedRequest();
    if (!item || !donorData) {
      els.notify.disabled = true;
      els.notifyLabel.textContent = "Alert donors";
      return;
    }
    radiusCircle = window.L.circle([hospital.lat, hospital.lng], {
      radius: donorData.radius_km * 1000,
      color: "#ff5a5f",
      weight: 1,
      dashArray: "4 6",
      fillOpacity: 0.04,
    }).addTo(map);
    for (const donor of donorData.donors) {
      const state = donor.pledged ? "pledged" : donor.notified ? "alerted" : "available";
      window.L.circleMarker([donor.approx_lat, donor.approx_lng], {
        radius: 6,
        color: "#0a0f15",
        weight: 2,
        fillColor: STATUS_COLOR[state],
        fillOpacity: 0.95,
      })
        .bindTooltip(`${donor.blood_group} · ${donor.distance_km} km · ${state}`)
        .addTo(donorLayer);
    }
    els.radius.value = String(donorData.radius_km);
    els.notify.disabled = donorData.notifiable === 0;
    els.notifyLabel.textContent = donorData.notifiable
      ? `Alert ${donorData.notifiable} ${donorData.notifiable === 1 ? "donor" : "donors"}`
      : "No new donors to alert";
  }

  async function loadDonors({ expand = false, fit = false } = {}) {
    const item = selectedRequest();
    if (!item) {
      donorData = null;
      renderDonors();
      return;
    }
    try {
      const params = new URLSearchParams({ radius: els.radius.value });
      if (expand) params.set("expand", "1");
      const data = await api(`${item.donors_url}?${params}`);
      if (selectedRequest()?.id !== item.id) return;
      donorData = data;
      lastDonorFetch = Date.now();
      renderDonors();
      if (fit && radiusCircle) map.fitBounds(radiusCircle.getBounds(), { padding: [24, 24] });
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function select(id, { focus = false } = {}) {
    selectedId = id;
    history.replaceState(null, "", `#request-${id}`);
    donorData = null;
    renderQueue();
    renderDetail();
    renderDonors();
    loadDonors({ expand: true, fit: true });
    if (focus) root.querySelector(`.queue-item[data-request-id="${id}"]`)?.focus();
  }

  function move(step) {
    if (!snapshot?.requests.length) return;
    const index = snapshot.requests.findIndex((item) => item.id === selectedId);
    const next = snapshot.requests[(index + step + snapshot.requests.length) % snapshot.requests.length];
    select(next.id, { focus: true });
  }

  async function refresh() {
    try {
      snapshot = await api(root.dataset.apiUrl);
      setLive(true);
      els.updated.textContent = clock(snapshot.generated_at);
      els.updated.dateTime = snapshot.generated_at;
      announceNewEvents();
      if (!selectedRequest() && snapshot.requests.length) {
        select(snapshot.requests[0].id);
      } else {
        if (!selectedRequest()) {
          selectedId = null;
          donorData = null;
          renderDonors();
        }
        renderQueue();
        renderDetail();
        if (selectedRequest() && Date.now() - lastDonorFetch > DONOR_REFRESH_MS) loadDonors();
      }
      renderSummary();
      renderFeed();
      seenEvents = new Set(snapshot.activity.map(eventKey));
      seenPledges = new Set(snapshot.requests.flatMap((item) => item.pledges.map((p) => `${p.id}:${p.status}`)));
      root.dataset.ready = "true";
    } catch (error) {
      setLive(false, error.message);
    }
  }

  async function alertDonors() {
    const item = selectedRequest();
    if (!item || !donorData || els.notify.disabled) return;
    if (!window.confirm(`${els.notifyLabel.textContent} for request #${item.id} by email now?`)) return;
    els.notify.disabled = true;
    els.notifyLabel.textContent = "Sending alerts…";
    try {
      const result = await api(item.notify_url, { method: "POST", body: { radius: donorData.radius_km } });
      if (result.message) toast(result.message, "info");
      else if (result.simulated) toast(`Demo mode: ${result.sent} alert(s) were logged instead of sent.`, "success");
      else toast(`Alerted ${result.sent} ${result.sent === 1 ? "donor" : "donors"}.`, result.failed ? "error" : "success");
    } catch (error) {
      toast(error.message, "error");
    }
    await refresh();
    loadDonors();
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else document.documentElement.requestFullscreen?.().catch(() => toast("Fullscreen isn't available here.", "error"));
  }

  // ---------- Events ----------
  els.radius.addEventListener("change", () => loadDonors({ fit: true }));
  els.notify.addEventListener("click", alertDonors);
  els.sound.addEventListener("click", toggleSound);
  els.fullscreen.addEventListener("click", toggleFullscreen);
  els.shortcutsOpen.addEventListener("click", () => els.shortcuts.showModal());
  document.addEventListener("fullscreenchange", () => {
    els.fullscreen.setAttribute("aria-pressed", String(Boolean(document.fullscreenElement)));
    window.setTimeout(() => map?.invalidateSize(), 200);
  });

  document.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target.closest("input, textarea, select, [contenteditable]") || els.shortcuts.open) return;
    const key = event.key.toLowerCase();
    const actions = {
      j: () => move(1),
      arrowdown: () => move(1),
      k: () => move(-1),
      arrowup: () => move(-1),
      a: alertDonors,
      r: () => {
        refresh();
        toast("Refreshed.", "info", 1500);
      },
      m: toggleSound,
      f: toggleFullscreen,
      "?": () => els.shortcuts.showModal(),
    };
    if (!(key in actions)) return;
    event.preventDefault();
    actions[key]();
  });

  renderSound();

  let timer = window.setInterval(refresh, REFRESH_MS);
  // ETAs tick down every 15 s even between server updates.
  window.setInterval(renderDetail, 15000);
  document.addEventListener("visibilitychange", () => {
    window.clearInterval(timer);
    if (!document.hidden) {
      refresh();
      timer = window.setInterval(refresh, REFRESH_MS);
    }
  });

  refresh();
})();
