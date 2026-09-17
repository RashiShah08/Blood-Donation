// Lets a user choose a location by tapping the map, dragging the pin or using GPS.
(() => {
  "use strict";

  const INDIA_CENTER = [20.5937, 78.9629];

  function isValid(lat, lng) {
    return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180;
  }

  function initPicker(root) {
    const latInput = root.querySelector('input[name="latitude"]');
    const lngInput = root.querySelector('input[name="longitude"]');
    const mapElement = root.querySelector(".picker-map");
    const status = root.querySelector(".picker-status");
    const locateButton = root.querySelector("[data-use-location]");

    const initialLat = parseFloat(latInput.value);
    const initialLng = parseFloat(lngInput.value);
    const hasInitial = isValid(initialLat, initialLng);
    const map = window.BloodConnect.createMap(mapElement, hasInitial ? [initialLat, initialLng] : INDIA_CENTER, hasInitial ? 14 : 5);
    if (!map) return;
    let marker = null;

    function setPoint(lat, lng, { pan = false } = {}) {
      const roundedLat = Number(lat.toFixed(6));
      const roundedLng = Number(lng.toFixed(6));
      latInput.value = roundedLat;
      lngInput.value = roundedLng;
      if (marker) {
        marker.setLatLng([roundedLat, roundedLng]);
      } else {
        marker = window.L.marker([roundedLat, roundedLng], { draggable: true, keyboard: true, title: "Selected location" }).addTo(map);
        marker.on("dragend", () => {
          const position = marker.getLatLng();
          setPoint(position.lat, position.lng);
        });
      }
      if (pan) map.setView([roundedLat, roundedLng], Math.max(map.getZoom(), 15));
      status.textContent = `Location set (${roundedLat.toFixed(4)}, ${roundedLng.toFixed(4)}).`;
      // Let live form helpers (progress, previews) know the location changed.
      latInput.dispatchEvent(new Event("input", { bubbles: true }));
    }

    if (hasInitial) setPoint(initialLat, initialLng);

    map.on("click", (event) => setPoint(event.latlng.lat, event.latlng.lng));

    [latInput, lngInput].forEach((input) =>
      input.addEventListener("change", () => {
        const lat = parseFloat(latInput.value);
        const lng = parseFloat(lngInput.value);
        if (isValid(lat, lng)) setPoint(lat, lng, { pan: true });
      }),
    );

    locateButton.addEventListener("click", () => {
      if (!navigator.geolocation) {
        status.textContent = "Your browser can't share its location. Tap the map instead.";
        return;
      }
      locateButton.disabled = true;
      status.textContent = "Finding your location…";
      navigator.geolocation.getCurrentPosition(
        (position) => {
          setPoint(position.coords.latitude, position.coords.longitude, { pan: true });
          locateButton.disabled = false;
        },
        (error) => {
          status.textContent =
            error.code === error.PERMISSION_DENIED
              ? "Location permission was denied. Tap the map to choose a location instead."
              : "We couldn't get your location. Tap the map to choose a location instead.";
          locateButton.disabled = false;
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
      );
    });

    // Leaflet needs a size refresh if the map was laid out while hidden or resized.
    window.setTimeout(() => map.invalidateSize(), 200);
  }

  document.querySelectorAll("[data-location-picker]").forEach(initPicker);
})();
