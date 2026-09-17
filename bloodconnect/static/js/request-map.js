// Hospital request page: search for nearby compatible donors and alert them.
(() => {
  "use strict";

  const root = document.getElementById("donor-search");
  if (!root) return;

  const { api, toast, createMap } = window.BloodConnect;
  const radiusSelect = root.querySelector("[data-radius]");
  const summary = root.querySelector("[data-summary]");
  const list = root.querySelector("[data-donor-list]");
  const notifyButton = root.querySelector("[data-notify]");
  const notifyLabel = root.querySelector("[data-notify-label]");
  const notifiedCount = document.querySelector("[data-notified-count]");

  const hospitalLat = parseFloat(root.dataset.hospitalLat);
  const hospitalLng = parseFloat(root.dataset.hospitalLng);
  const map = createMap(root.querySelector("[data-map]"), [hospitalLat, hospitalLng], 13);
  if (!map) return;

  window.L.marker([hospitalLat, hospitalLng], { title: root.dataset.hospitalName })
    .addTo(map)
    .bindPopup(root.dataset.hospitalName);
  const donorLayer = window.L.layerGroup().addTo(map);
  let radiusCircle = null;
  let currentRadius = Number(radiusSelect.value);
  let searchToken = 0;

  function statusText(donor) {
    if (donor.pledged) return "Pledged";
    if (donor.notified) return "Alerted";
    return "Not alerted yet";
  }

  function render(data) {
    currentRadius = data.radius_km;
    radiusSelect.value = String(data.radius_km);

    donorLayer.clearLayers();
    if (radiusCircle) radiusCircle.remove();
    radiusCircle = window.L.circle([hospitalLat, hospitalLng], {
      radius: data.radius_km * 1000,
      color: "#b3121f",
      weight: 2,
      fillOpacity: 0.06,
    }).addTo(map);
    map.fitBounds(radiusCircle.getBounds(), { padding: [16, 16] });

    list.replaceChildren();
    data.donors.forEach((donor) => {
      const color = donor.pledged ? "#15803d" : donor.notified ? "#92400e" : "#b3121f";
      window.L.circleMarker([donor.approx_lat, donor.approx_lng], {
        radius: 8,
        color: "#fff",
        weight: 2,
        fillColor: color,
        fillOpacity: 0.9,
      })
        .bindTooltip(`${donor.blood_group} donor · about ${donor.distance_km} km · ${statusText(donor)}`)
        .addTo(donorLayer);

      const item = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = `${donor.blood_group} donor · ${donor.distance_km} km`;
      const state = document.createElement("span");
      state.className = "muted";
      state.textContent = statusText(donor);
      item.append(label, state);
      list.appendChild(item);
    });

    const count = data.donors.length;
    if (count === 0) {
      summary.textContent = `No available compatible donors within ${data.radius_km} km. Try a wider radius.`;
    } else {
      summary.textContent =
        `${count} compatible ${count === 1 ? "donor" : "donors"} within ${data.radius_km} km` +
        ` · ${data.notifiable} not alerted yet.`;
    }

    const canNotify = data.request.status === "open" && data.notifiable > 0;
    notifyButton.disabled = !canNotify;
    notifyLabel.textContent = canNotify
      ? `Alert ${data.notifiable} ${data.notifiable === 1 ? "donor" : "donors"}`
      : "No new donors to alert";
  }

  async function search({ expand = false } = {}) {
    const token = ++searchToken;
    summary.textContent = "Searching for donors…";
    if (!list.children.length || list.querySelector(".skeleton-row")) {
      list.replaceChildren(
        ...Array.from({ length: 4 }, () => {
          const row = document.createElement("li");
          row.className = "skeleton-row";
          row.setAttribute("aria-hidden", "true");
          row.innerHTML = '<span class="skeleton" style="width: 45%"></span><span class="skeleton" style="width: 25%"></span>';
          return row;
        }),
      );
    }
    notifyButton.disabled = true;
    try {
      const params = new URLSearchParams({ radius: radiusSelect.value });
      if (expand) params.set("expand", "1");
      const data = await api(`${root.dataset.donorsUrl}?${params}`);
      if (token === searchToken) render(data);
    } catch (error) {
      if (token !== searchToken) return;
      summary.textContent = error.message;
      toast(error.message, "error");
    }
  }

  notifyButton.addEventListener("click", async () => {
    const count = notifyLabel.textContent;
    if (!window.confirm(`${count} by email now? Each donor is only alerted once per request.`)) return;
    notifyButton.disabled = true;
    notifyLabel.textContent = "Sending alerts…";
    try {
      const result = await api(root.dataset.notifyUrl, { method: "POST", body: { radius: currentRadius } });
      if (result.message) {
        toast(result.message, "info");
      } else if (result.simulated) {
        toast(`Demo mode: email sending is off, so ${result.sent} alert(s) were logged instead of sent.`, "success");
      } else {
        const failed = result.failed ? ` ${result.failed} couldn't be delivered.` : "";
        toast(`Alerted ${result.sent} ${result.sent === 1 ? "donor" : "donors"}.${failed}`, result.failed ? "error" : "success");
      }
      if (notifiedCount) notifiedCount.textContent = String(Number(notifiedCount.textContent) + result.sent);
    } catch (error) {
      toast(error.message, "error");
    }
    search();
  });

  radiusSelect.addEventListener("change", () => search());
  search({ expand: true });
})();
