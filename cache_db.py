"""
cache_db.py

SQLite caching layer for:
  - Geocoded locations  (place_name → lat, lon, address, cached_at)
    cached_at is a Unix timestamp (int). Entries older than CACHE_TTL_DAYS
    are treated as expired to comply with Mapbox standard-tier ToS.
  - Pairwise distances  (lat1,lon1,lat2,lon2 → distance_m in Haversine metres)

Functions
─────────
  init_db()                → create/migrate tables
  get_cached_location()    → TTL-aware single-row lookup
  save_location()          → upsert with timestamp
  purge_expired_locations()→ delete stale rows at startup
  get_all_locations()      → bulk-load all valid rows (for Trie)
  migrate_legacy_db()      → import rows from old route_cache.db
"""

import sqlite3
import time

DB_NAME        = "delivery_cache.db"
CACHE_TTL_DAYS = 30          # Mapbox standard ToS allows up to 30-day caching


# ─────────────────────────────────────────────
# Schema initialisation
# ─────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # Geocoded locations — includes cached_at (Unix timestamp) for TTL enforcement
    cur.execute("""
    CREATE TABLE IF NOT EXISTS locations (
        place_name  TEXT PRIMARY KEY,
        lat         REAL NOT NULL,
        lon         REAL NOT NULL,
        address     TEXT,
        cached_at   INTEGER
    )
    """)

    # Safe migration: add cached_at to pre-existing tables that lack the column.
    # SQLite raises OperationalError if the column already exists — we ignore it.
    try:
        cur.execute("ALTER TABLE locations ADD COLUMN cached_at INTEGER")
    except sqlite3.OperationalError:
        pass  # column already present

    # Pairwise distances computed by Haversine
    cur.execute("""
    CREATE TABLE IF NOT EXISTS distances (
        lat1        REAL NOT NULL,
        lon1        REAL NOT NULL,
        lat2        REAL NOT NULL,
        lon2        REAL NOT NULL,
        distance_m  REAL NOT NULL,
        PRIMARY KEY (lat1, lon1, lat2, lon2)
    )
    """)

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# Location cache
# ─────────────────────────────────────────────

def get_cached_location(place: str, ttl_days: int = CACHE_TTL_DAYS):
    """
    Return (lat, lon, address) or None.

    Returns None if:
      - No row exists for this place name, OR
      - The cached_at timestamp is NULL (legacy row pre-dating TTL), OR
      - The row is older than ttl_days (expired — must re-fetch from Mapbox).
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT lat, lon, address, cached_at FROM locations WHERE place_name = ?",
        (place,)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    lat, lon, address, cached_at = row

    # Legacy rows (cached_at IS NULL) pre-date the TTL system — treat as expired
    if cached_at is None:
        return None

    age_days = (time.time() - cached_at) / 86400
    if age_days > ttl_days:
        return None   # expired — caller will re-fetch from Mapbox

    return (lat, lon, address)


def save_location(place: str, lat: float, lon: float, address: str):
    """Save a geocoded location with the current Unix timestamp."""
    now = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO locations VALUES (?, ?, ?, ?, ?)",
        (place, lat, lon, address, now)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# Cache maintenance
# ─────────────────────────────────────────────

def purge_expired_locations(ttl_days: int = CACHE_TTL_DAYS):
    """
    Delete all location rows older than ttl_days, plus legacy rows with no
    cached_at timestamp. Call once at server startup to keep the database clean.
    """
    cutoff = int(time.time()) - (ttl_days * 86400)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM locations WHERE cached_at IS NULL OR cached_at < ?",
        (cutoff,)
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"  Cache: purged {deleted} expired location(s) (>{ttl_days} days old)")
    else:
        print(f"  Cache: all location entries are within the {ttl_days}-day TTL — nothing purged")


# ─────────────────────────────────────────────
# Trie support
# ─────────────────────────────────────────────

def get_all_locations() -> list[tuple]:
    """
    Return all non-expired location rows as a list of
    (place_name, lat, lon, address) tuples.

    Called once at startup to bulk-populate the in-memory Trie.
    """
    cutoff = int(time.time()) - (CACHE_TTL_DAYS * 86400)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT place_name, lat, lon, address FROM locations "
        "WHERE cached_at IS NOT NULL AND cached_at >= ?",
        (cutoff,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def migrate_legacy_db(legacy_db: str = "route_cache.db") -> int:
    """
    One-time migration: import location rows from the old route_cache.db
    (which used 'latitude'/'longitude' column names) into the current
    delivery_cache.db schema.  Already-present rows are skipped.

    Returns the number of rows successfully imported.
    """
    import os
    if not os.path.exists(legacy_db):
        return 0

    try:
        legacy = sqlite3.connect(legacy_db)
        cur    = legacy.cursor()
        cur.execute("SELECT place_name, latitude, longitude, address FROM locations")
        old_rows = cur.fetchall()
        legacy.close()
    except Exception as exc:
        print(f"  Migration: could not read {legacy_db} — {exc}")
        return 0

    if not old_rows:
        return 0

    now  = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    cur  = conn.cursor()
    migrated = 0
    for place_name, lat, lon, address in old_rows:
        cur.execute("SELECT 1 FROM locations WHERE place_name = ?", (place_name,))
        if not cur.fetchone():
            cur.execute(
                "INSERT OR IGNORE INTO locations VALUES (?, ?, ?, ?, ?)",
                (place_name, lat, lon, address, now)
            )
            migrated += 1
    conn.commit()
    conn.close()
    return migrated


# ─────────────────────────────────────────────
# Distance cache
# ─────────────────────────────────────────────

def _normalise(lat1, lon1, lat2, lon2):
    """Always store the smaller coordinate pair first so A→B and B→A share one row."""
    a = (round(lat1, 6), round(lon1, 6))
    b = (round(lat2, 6), round(lon2, 6))
    if a > b:
        a, b = b, a
    return a[0], a[1], b[0], b[1]


def get_cached_distance(lat1: float, lon1: float,
                         lat2: float, lon2: float) -> float | None:
    """Return cached Haversine distance in metres, or None on miss."""
    n1, n2, n3, n4 = _normalise(lat1, lon1, lat2, lon2)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT distance_m FROM distances WHERE lat1=? AND lon1=? AND lat2=? AND lon2=?",
        (n1, n2, n3, n4)
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def save_distance(lat1: float, lon1: float,
                  lat2: float, lon2: float,
                  distance_m: float):
    n1, n2, n3, n4 = _normalise(lat1, lon1, lat2, lon2)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO distances VALUES (?, ?, ?, ?, ?)",
        (n1, n2, n3, n4, distance_m)
    )
    conn.commit()
    conn.close()
