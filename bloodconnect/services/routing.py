"""Driving routes via OpenRouteService, called from the server so the API key stays secret."""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache

from ..domain import geo

log = logging.getLogger(__name__)

ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"
FALLBACK_SPEED_KMH = 30


@dataclass(frozen=True)
class Route:
    distance_km: float
    duration_min: float
    coordinates: tuple[tuple[float, float], ...]  # (lat, lon) pairs
    source: str  # "openrouteservice" or "straight_line"

    def to_dict(self) -> dict:
        return {
            "distance_km": round(self.distance_km, 2),
            "duration_min": round(self.duration_min, 1),
            "coordinates": [list(point) for point in self.coordinates],
            "source": self.source,
        }


def straight_line_route(start: tuple[float, float], end: tuple[float, float]) -> Route:
    distance = geo.haversine_km(*start, *end)
    return Route(distance, distance / FALLBACK_SPEED_KMH * 60, (start, end), "straight_line")


@lru_cache(maxsize=256)
def _ors_route(start: tuple[float, float], end: tuple[float, float], api_key: str, timeout: float) -> Route:
    query = f"?start={start[1]},{start[0]}&end={end[1]},{end[0]}"
    headers = {"Authorization": api_key, "Accept": "application/geo+json"}
    req = urllib.request.Request(ORS_DIRECTIONS_URL + query, headers=headers)  # noqa: S310 (fixed https URL)
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310
        payload = json.load(response)
    feature = payload["features"][0]
    summary = feature["properties"]["summary"]
    coordinates = tuple((lat, lon) for lon, lat in feature["geometry"]["coordinates"])
    return Route(summary["distance"] / 1000, summary["duration"] / 60, coordinates, "openrouteservice")


def get_route(start: tuple[float, float], end: tuple[float, float], api_key: str | None, timeout: float = 8) -> Route:
    """Route between two (lat, lon) points. Falls back to a straight line if ORS is unavailable."""
    if not api_key:
        return straight_line_route(start, end)
    # Round to ~11 m so small GPS jitter reuses the cached route.
    rounded_start = (round(start[0], 4), round(start[1], 4))
    rounded_end = (round(end[0], 4), round(end[1], 4))
    try:
        return _ors_route(rounded_start, rounded_end, api_key, timeout)
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
        log.warning("OpenRouteService request failed; using a straight-line estimate", exc_info=True)
        return straight_line_route(start, end)
