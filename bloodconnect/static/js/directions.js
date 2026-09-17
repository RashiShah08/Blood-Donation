// Donor directions: live position, a route fetched through our server, and arrival detection.
(() => {
  "use strict";

  const root = document.getElementById("directions");
  if (!root) return;

  const { api, toast, createMap, distanceKm, formatDistance } = window.BloodConnect;
  const ARRIVAL_RADIUS_KM = 0.1;
  const OFF_ROUTE_KM = 0.2;
  const REROUTE_INTERVAL_MS = 2 * 60 * 1000;
  const FALLBACK_SPEED_KMH = 30;

  const hospital = {
    lat: parseFloat(root.dataset.hospitalLat),
    lng: parseFloat(root.dataset.hospitalLng),
    name: root.dataset.hospitalName,
  };
  const map = createMap(root.querySelector("[data-map]"), [hospital.lat, hospital.lng], 14);
  if (!map) return;
  window.L.marker([hospital.lat, hospital.lng], { title: hospital.name }).addTo(map).bindPopup(hospital.name).openPopup();

  const statusEl = root.querySelector("[data-trip-status]");
  const stats = {
    distance: root.querySelector('[data-stat="distance"]'),
    eta: root.querySelector('[data-stat="eta"]'),
    speed: root.querySelector('[data-stat="speed"]'),
    progress: root.querySelector('[data-stat="progress"]'),
  };
  const routeSourceEl = root.querySelector("[data-route-source]");
  const arrivalEl = root.querySelector("[data-arrival]");
  const buttons = {
    start: root.querySelector('[data-action="start"]'),
    stop: root.querySelector('[data-action="stop"]'),
    recenter: root.querySelector('[data-action="recenter"]'),
    share: root.querySelector('[data-action="share"]'),
  };

  let watchId = null;
  let donorMarker = null;
  let routeLine = null;
  let route = null; // { coordinates, distanceKm, durationMin, startRemainingKm }
  let lastRouteAt = 0;
  let routing = false;
  let lastFix = null; // { lat, lng, time }
  let initialRemainingKm = null;
  let arrived = root.dataset.alreadyArrived === "true";

  function distanceFromRoute(lat, lng) {
    if (!route || route.coordinates.length === 0) return Infinity;
    return Math.min(...route.coordinates.map(([pLat, pLng]) => distanceKm(lat, lng, pLat, pLng)));
  }

  async function fetchRoute(lat, lng, remainingKm) {
    routing = true;
    try {
      const params = new URLSearchParams({ lat: lat.toFixed(6), lng: lng.toFixed(6) });
      const data = await api(`${root.dataset.routeUrl}?${params}`);
      route = {
        coordinates: data.route.coordinates,
        distanceKm: data.route.distance_km,
        durationMin: data.route.duration_min,
        startRemainingKm: remainingKm,
      };
      if (routeLine) routeLine.remove();
      routeLine = window.L.polyline(route.coordinates, { color: "#b3121f", weight: 5, opacity: 0.85 }).addTo(map);
      routeSourceEl.textContent =
        data.route.source === "openrouteservice"
          ? `Road route: ${data.route.distance_km} km`
          : "Straight-line estimate (road routing is unavailable right now).";
    } catch (error) {
      toast(error.message, "error");
    } finally {
      lastRouteAt = Date.now();
      routing = false;
    }
  }

  // Share only ETA and distance with the hospital, at most every 30 s unless the ETA moves by 2+ minutes.
  let lastReport = { at: 0, eta: null };
  function reportProgress(remainingKm, etaMinutes) {
    const now = Date.now();
    const changed = lastReport.eta === null || Math.abs(etaMinutes - lastReport.eta) >= 2;
    if (now - lastReport.at < 30000 && !changed) return;
    lastReport = { at: now, eta: etaMinutes };
    api(root.dataset.progressUrl, {
      method: "POST",
      body: { distance_km: Number(remainingKm.toFixed(2)), eta_minutes: etaMinutes },
    }).catch(() => {
      /* progress is best-effort; directions keep working offline */
    });
  }

  function updateStats(remainingKm, speedKmh) {
    stats.distance.textContent = formatDistance(remainingKm);
    let etaMinutes;
    if (route && route.startRemainingKm > 0) {
      etaMinutes = Math.max(1, Math.round(route.durationMin * Math.min(1, remainingKm / route.startRemainingKm)));
    } else {
      etaMinutes = Math.max(1, Math.round((remainingKm / FALLBACK_SPEED_KMH) * 60));
    }
    stats.eta.textContent = `${etaMinutes} min`;
    if (remainingKm > ARRIVAL_RADIUS_KM) reportProgress(remainingKm, etaMinutes);
    stats.speed.textContent = speedKmh == null ? "—" : `${Math.round(speedKmh)} km/h`;
    if (initialRemainingKm) {
      stats.progress.value = Math.max(0, Math.min(100, ((initialRemainingKm - remainingKm) / initialRemainingKm) * 100));
    }
  }

  async function handleArrival() {
    arrived = true;
    stopTracking("You've arrived.");
    stats.progress.value = 100;
    arrivalEl.hidden = false;
    toast("You've arrived at the hospital. Thank you!", "success");
    try {
      await api(root.dataset.arrivedUrl, { method: "POST" });
    } catch {
      /* arrival is informational; ignore network errors */
    }
  }

  function onPosition(position) {
    const { latitude: lat, longitude: lng, speed } = position.coords;
    const now = Date.now();

    let speedKmh = null;
    if (speed != null && speed >= 0) {
      speedKmh = speed * 3.6;
    } else if (lastFix && now - lastFix.time > 1000) {
      speedKmh = distanceKm(lastFix.lat, lastFix.lng, lat, lng) / ((now - lastFix.time) / 3600000);
    }
    lastFix = { lat, lng, time: now };

    if (donorMarker) {
      donorMarker.setLatLng([lat, lng]);
    } else {
      const icon = window.L.divIcon({ className: "donor-marker", iconSize: [18, 18] });
      donorMarker = window.L.marker([lat, lng], { icon, title: "Your location" }).addTo(map);
      map.fitBounds(window.L.latLngBounds([[lat, lng], [hospital.lat, hospital.lng]]), { padding: [40, 40] });
    }

    const remainingKm = distanceKm(lat, lng, hospital.lat, hospital.lng);
    if (initialRemainingKm == null) initialRemainingKm = remainingKm;

    if (remainingKm <= ARRIVAL_RADIUS_KM) {
      updateStats(remainingKm, speedKmh);
      if (!arrived) handleArrival();
      return;
    }

    const needsRoute =
      !routing && (!route || now - lastRouteAt > REROUTE_INTERVAL_MS || distanceFromRoute(lat, lng) > OFF_ROUTE_KM);
    if (needsRoute) fetchRoute(lat, lng, remainingKm);

    statusEl.textContent = `Heading to ${hospital.name}.`;
    updateStats(remainingKm, speedKmh);
  }

  function onPositionError(error) {
    const message =
      error.code === error.PERMISSION_DENIED
        ? "Location permission was denied. Allow location access to see live directions."
        : "We couldn't get your location. Make sure GPS is on.";
    statusEl.textContent = message;
    toast(message, "error");
    if (error.code === error.PERMISSION_DENIED) stopTracking(message);
  }

  function startTracking() {
    if (!navigator.geolocation) {
      toast("Your browser can't share its location.", "error");
      return;
    }
    arrived = false;
    arrivalEl.hidden = true;
    statusEl.textContent = "Getting your location…";
    watchId = navigator.geolocation.watchPosition(onPosition, onPositionError, {
      enableHighAccuracy: true,
      maximumAge: 5000,
      timeout: 15000,
    });
    buttons.start.hidden = true;
    buttons.stop.hidden = false;
  }

  function stopTracking(message = "Tracking paused.") {
    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    statusEl.textContent = message;
    if (buttons.start) buttons.start.hidden = false;
    if (buttons.stop) buttons.stop.hidden = true;
  }

  buttons.start?.addEventListener("click", startTracking);
  buttons.stop?.addEventListener("click", () => stopTracking());
  buttons.recenter?.addEventListener("click", () => {
    const target = donorMarker ? donorMarker.getLatLng() : [hospital.lat, hospital.lng];
    map.setView(target, 15);
  });
  buttons.share?.addEventListener("click", async () => {
    if (!lastFix) {
      toast("Start tracking first so we know where you are.", "info");
      return;
    }
    const url = `https://www.openstreetmap.org/?mlat=${lastFix.lat.toFixed(5)}&mlon=${lastFix.lng.toFixed(5)}#map=16/${lastFix.lat.toFixed(5)}/${lastFix.lng.toFixed(5)}`;
    const text = `I'm on my way to ${hospital.name} to donate blood.`;
    try {
      if (navigator.share) {
        await navigator.share({ title: "My location", text, url });
      } else {
        await navigator.clipboard.writeText(`${text} ${url}`);
        toast("Location link copied to your clipboard.", "success");
      }
    } catch (error) {
      if (error.name !== "AbortError") toast("Couldn't share your location.", "error");
    }
  });

  window.addEventListener("pagehide", () => stopTracking());
})();
