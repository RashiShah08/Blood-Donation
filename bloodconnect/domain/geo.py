"""Distance helpers for matching donors and hospitals."""

import math

EARTH_RADIUS_KM = 6371.0088


def is_valid_coordinate(lat: object, lon: object) -> bool:
    if isinstance(lat, bool) or isinstance(lon, bool):
        return False
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return False
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float | None, float | None]:
    """A lat/lon box that contains every point within radius_km, for a cheap SQL pre-filter.

    Returns (min_lat, max_lat, min_lon, max_lon). The longitude bounds are None when the
    box would cross the poles or the antimeridian, meaning "don't filter by longitude".
    """
    d_lat = math.degrees(radius_km / EARTH_RADIUS_KM)
    min_lat, max_lat = lat - d_lat, lat + d_lat
    cos_lat = math.cos(math.radians(lat))
    if min_lat <= -90 or max_lat >= 90 or cos_lat < 1e-6:
        return max(min_lat, -90.0), min(max_lat, 90.0), None, None
    d_lon = math.degrees(radius_km / (EARTH_RADIUS_KM * cos_lat))
    min_lon, max_lon = lon - d_lon, lon + d_lon
    if min_lon < -180 or max_lon > 180:
        return min_lat, max_lat, None, None
    return min_lat, max_lat, min_lon, max_lon


def approximate(lat: float, lon: float, decimals: int = 2) -> tuple[float, float]:
    """Round a position (2 decimals is roughly 1 km) so an exact home address isn't revealed."""
    return round(lat, decimals), round(lon, decimals)
