"""
osrm_client.py

Thin wrapper around the OSRM public API (router.project-osrm.org).
OSRM implements Contraction Hierarchies (CH) — the same algorithm Google Maps uses
for shortest-path queries. This replaces OSMnx A*, which cannot handle large areas.

Endpoints used
──────────────
- /route/v1/driving   — full road polyline + duration + distance for a sequence of coords
- /table/v1/driving   — NxN road-distance matrix for multiple coords

SQLite caching
──────────────
- Individual pair distances cached in `delivery_cache.db` (osrm_distances table)
- Full route polylines cached by stop hash in `delivery_cache.db` (osrm_routes table)
"""

import json
import hashlib
import requests
import sqlite3
import time

OSRM_BASE = "http://router.project-osrm.org"
DB_NAME   = "delivery_cache.db"

# ─────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────

def _conn():
    return sqlite3.connect(DB_NAME)


def _ensure_tables():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS osrm_distances (
                lat1 REAL, lon1 REAL, lat2 REAL, lon2 REAL,
                distance_m REAL, duration_s REAL,
                PRIMARY KEY (lat1, lon1, lat2, lon2)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS osrm_routes (
                stops_hash TEXT PRIMARY KEY,
                polyline   TEXT,
                distance_m REAL,
                duration_s REAL
            )
        """)


def _stops_hash(coords: list[tuple[float, float]]) -> str:
    key = json.dumps(coords, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:20]


# ─────────────────────────────────────────────
# OSRM HTTP helpers
# ─────────────────────────────────────────────

def _coords_str(coords: list[tuple[float, float]]) -> str:
    """OSRM expects lon,lat order."""
    return ";".join(f"{lon},{lat}" for lat, lon in coords)


def _get(url: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 ** attempt)
    return {}


# ─────────────────────────────────────────────
# Decode OSRM encoded polyline (Google format)
# ─────────────────────────────────────────────

def _decode_polyline(encoded: str) -> list[list[float]]:
    """Decode Google-encoded polyline to list of [lat, lon]."""
    coords = []
    index = lat = lng = 0
    while index < len(encoded):
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append([lat / 1e5, lng / 1e5])
    return coords


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def get_road_distance(lat1: float, lon1: float,
                      lat2: float, lon2: float) -> tuple[float, float]:
    """
    Return (distance_m, duration_s) for a single pair via OSRM.
    Results are cached in SQLite.
    """
    _ensure_tables()

    # Normalise for symmetric caching
    a = (round(lat1, 6), round(lon1, 6))
    b = (round(lat2, 6), round(lon2, 6))
    la1, lo1, la2, lo2 = (*min(a, b), *max(a, b))

    with _conn() as c:
        row = c.execute(
            "SELECT distance_m, duration_s FROM osrm_distances "
            "WHERE lat1=? AND lon1=? AND lat2=? AND lon2=?",
            (la1, lo1, la2, lo2)
        ).fetchone()
    if row:
        return row[0], row[1]

    url = f"{OSRM_BASE}/route/v1/driving/{lo1},{la1};{lo2},{la2}?overview=false"
    data = _get(url)

    dist = data["routes"][0]["distance"]
    dur  = data["routes"][0]["duration"]

    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO osrm_distances VALUES (?,?,?,?,?,?)",
            (la1, lo1, la2, lo2, dist, dur)
        )

    return dist, dur


def get_road_polyline(ordered_coords: list[tuple[float, float]]) -> dict:
    """
    Return road-following polyline for an ordered sequence of coordinates.
    Uses OSRM /route endpoint (CH-powered).

    Returns:
        {
          "polyline": [[lat, lon], ...],
          "distance_m": float,
          "duration_s": float,
          "legs": [{"distance_m": float, "duration_s": float}, ...]
        }
    """
    _ensure_tables()

    h = _stops_hash(ordered_coords)
    with _conn() as c:
        row = c.execute(
            "SELECT polyline, distance_m, duration_s FROM osrm_routes WHERE stops_hash=?",
            (h,)
        ).fetchone()
    if row:
        return {
            "polyline":   json.loads(row[0]),
            "distance_m": row[1],
            "duration_s": row[2],
            "legs":       []
        }

    coords_str = _coords_str(ordered_coords)
    url = (f"{OSRM_BASE}/route/v1/driving/{coords_str}"
           f"?overview=full&geometries=polyline&steps=false")
    data = _get(url)

    route    = data["routes"][0]
    polyline = _decode_polyline(route["geometry"])
    dist     = route["distance"]
    dur      = route["duration"]
    legs     = [{"distance_m": leg["distance"], "duration_s": leg["duration"]}
                for leg in route.get("legs", [])]

    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO osrm_routes VALUES (?,?,?,?)",
            (h, json.dumps(polyline), dist, dur)
        )

    return {
        "polyline":   polyline,
        "distance_m": dist,
        "duration_s": dur,
        "legs":       legs
    }


def build_distance_matrix_osrm(coords: list[tuple[float, float]]) -> list[list[float]]:
    """
    Build NxN road-distance matrix using OSRM /table endpoint (Contraction Hierarchies).
    Much more accurate than Haversine for TSP optimisation.
    """
    _ensure_tables()
    n = len(coords)
    if n == 0:
        return []

    coords_str = _coords_str(coords)
    url = f"{OSRM_BASE}/table/v1/driving/{coords_str}?annotations=distance"
    data = _get(url)

    durations = data.get("distances") or data.get("durations", [])
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            matrix[i][j] = float(durations[i][j] or 0.0)

    return matrix


def snap_to_route(user_lat: float, user_lon: float,
                  remaining_coords: list[tuple[float, float]]) -> dict:
    """
    Given the user's current GPS position and remaining stops,
    return an updated polyline from user position → remaining stops.
    """
    if not remaining_coords:
        return {"polyline": [], "distance_m": 0, "duration_s": 0, "legs": []}

    all_coords = [(user_lat, user_lon)] + remaining_coords
    return get_road_polyline(all_coords)
