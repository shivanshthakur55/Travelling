"""
distance_matrix.py

Builds an NxN distance matrix using the Haversine formula.
Every computed pair is cached in SQLite; repeated runs return instantly.
"""

import math
from cache_db import get_cached_distance, save_distance


# ─────────────────────────────────────────────
# Haversine
# ─────────────────────────────────────────────

EARTH_RADIUS_M = 6_371_000  # metres


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance between two (lat, lon) points in metres.
    Fast pure-Python implementation — ~0.01 ms per call.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)

    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ─────────────────────────────────────────────
# Matrix builder
# ─────────────────────────────────────────────

def build_matrix(coords: list[tuple[float, float]]) -> list[list[float]]:
    """
    Build and return a symmetric NxN distance matrix (metres).

    coords : list of (lat, lon) tuples
    Returns: matrix[i][j] = haversine distance from coords[i] to coords[j]

    Cache behaviour
    ───────────────
    - Hit  → read from SQLite  (<1 ms)
    - Miss → compute Haversine (~0.01 ms) → write to SQLite
    """
    n = len(coords)
    matrix = [[0.0] * n for _ in range(n)]

    hits = 0
    misses = 0

    for i in range(n):
        for j in range(n):
            if i == j:
                continue

            lat1, lon1 = coords[i]
            lat2, lon2 = coords[j]

            cached = get_cached_distance(lat1, lon1, lat2, lon2)

            if cached is not None:
                matrix[i][j] = cached
                hits += 1
            else:
                dist = haversine(lat1, lon1, lat2, lon2)
                save_distance(lat1, lon1, lat2, lon2, dist)
                matrix[i][j] = dist
                misses += 1

    total = hits + misses
    if total > 0:
        print(f"  Distance matrix: {n}x{n} — "
              f"{hits} cache hits, {misses} computed")

    return matrix
