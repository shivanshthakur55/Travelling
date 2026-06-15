"""
app.py

Flask web server — the new entry point for the Route Optimizer.

Endpoints
─────────
GET  /                   → serve the Google Maps-style SPA
GET  /suggest?q=...      → Trie lookup → Photon fallback (free, no rate limits)
POST /geocode            → geocode a place name → {lat, lon, address}
POST /route              → full route optimization → route JSON
                            Supports optional user_location:{lat,lon}
POST /reroute            → same as /route, called when a stop is added mid-route
POST /snap_route         → re-route from current GPS position → updated polyline

Geocoding stack (fastest → slowest)
───────────────────────────────────
  1. In-memory Trie           → O(k) prefix lookup, 0ms, 0 API calls
  2. SQLite cache (30-day TTL)→ <1ms, 0 API calls
  3. Photon API               → free, ~100ms (autocomplete fallback)
  4. Mapbox Geocoding API     → paid quota, ~150ms (geocoding fallback)

Run with:  python app.py
Then open: http://localhost:5000
"""

import os
import sys
import traceback
import requests as _requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from geopy.geocoders import MapBox

import cache_db
import ga_tsp
import osrm_client
from trie import LocationTrie

# ─────────────────────────────────────────────
# Config — loaded from .env
# ─────────────────────────────────────────────

load_dotenv()
MAPBOX_TOKEN = os.getenv("MAPBOX_ACCESS_TOKEN", "").strip()

if not MAPBOX_TOKEN:
    print("[WARNING] MAPBOX_ACCESS_TOKEN not set in .env — geocoding will fail.")

app = Flask(__name__)
CORS(app)

_geocoder = MapBox(api_key=MAPBOX_TOKEN) if MAPBOX_TOKEN else None

# In-memory Trie — populated at startup from SQLite, grows with every new geocode
_location_trie = LocationTrie()


# ─────────────────────────────────────────────
# Trie builder
# ─────────────────────────────────────────────

def _build_trie() -> None:
    """
    Bulk-populate the in-memory Trie from all non-expired SQLite rows.
    Called once at server startup after init_db() and purge_expired_locations().
    """
    rows = cache_db.get_all_locations()
    for place_name, lat, lon, address in rows:
        _location_trie.insert_location(place_name, lat, lon, address)
    if rows:
        print(f"  Trie: loaded {_location_trie.size} entries "
              f"from {len(rows)} cached location(s)")
    else:
        print("  Trie: empty — will grow as locations are geocoded")
# Run database initialization and trie-building at startup (important for Gunicorn/production)
cache_db.init_db()
migrated = cache_db.migrate_legacy_db()
if migrated:
    print(f"  Migration: imported {migrated} location(s) from legacy route_cache.db")
cache_db.purge_expired_locations()
_build_trie()


# ─────────────────────────────────────────────
# Geocoding helper
# ─────────────────────────────────────────────

def geocode_place(place: str) -> dict:
    """
    Geocode a place name.

    Lookup order:
      1. SQLite cache (30-day TTL) — fastest
      2. Mapbox Geocoding API      — saves result to SQLite + inserts into Trie
    """
    cached = cache_db.get_cached_location(place)
    if cached:
        return {"lat": cached[0], "lon": cached[1], "address": cached[2], "cached": True}

    if not _geocoder:
        raise ValueError("MAPBOX_ACCESS_TOKEN is not set in .env")

    loc = _geocoder.geocode(place, timeout=15)
    if not loc:
        raise ValueError(f"Location not found: '{place}'")

    cache_db.save_location(place, loc.latitude, loc.longitude, loc.address)

    # Keep the Trie in sync so the next autocomplete query finds this place instantly
    _location_trie.insert_location(place, loc.latitude, loc.longitude, loc.address)

    return {
        "lat":     loc.latitude,
        "lon":     loc.longitude,
        "address": loc.address,
        "cached":  False
    }


# ─────────────────────────────────────────────
# Route computation helper
# ─────────────────────────────────────────────

def compute_route(places: list[str],
                  user_location: dict | None = None,
                  pre_resolved: list[dict] | None = None) -> dict:
    """
    Full pipeline:
    1. Resolve all places → (lat, lon, address)
       - If pre_resolved[i] has lat/lon (from autocomplete), use it directly.
       - Otherwise geocode the place name via Nominatim.
    2. Build NxN road-distance matrix via OSRM CH
    3. Solve open TSP via Genetic Algorithm (warehouse pinned as start)
    4. If user_location provided, prepend it: user → warehouse → stops
    5. Fetch road polyline for the full ordered sequence via OSRM CH
    6. Return complete route payload

    pre_resolved: list parallel to `places`. Each entry is either:
      {"lat": float, "lon": float, "address": str}  → use directly (no API call)
      None or {}                                      → geocode from name
    """
    if pre_resolved is None:
        pre_resolved = [None] * len(places)

    # Resolve each place — use pre-resolved coords if available, else geocode
    coords   = []
    resolved = []
    for i, p in enumerate(places):
        pr = (pre_resolved[i] if i < len(pre_resolved) else None) or {}
        if pr.get("lat") and pr.get("lon"):
            # Autocomplete already gave us exact coords — use them, skip Nominatim
            lat = float(pr["lat"])
            lon = float(pr["lon"])
            coords.append((lat, lon))
            resolved.append({
                "name":    p,
                "lat":     lat,
                "lon":     lon,
                "address": pr.get("address", p),
            })
        else:
            # Fall back to Nominatim geocoding (cached in SQLite)
            g = geocode_place(p)
            coords.append((g["lat"], g["lon"]))
            resolved.append({"name": p, "lat": g["lat"], "lon": g["lon"], "address": g["address"]})

    # Distance matrix for TSP (warehouse + delivery stops only)
    matrix = osrm_client.build_distance_matrix_osrm(coords)


    # GA TSP — warehouse (index 0) is pinned as first stop
    order = ga_tsp.solve_tsp(matrix, start_index=0)

    # Optimised delivery sequence: warehouse → stops
    ordered = [resolved[i] for i in order]
    ordered_coords = [(r["lat"], r["lon"]) for r in ordered]

    # Total TSP distance (warehouse-only matrix)
    total_m = sum(matrix[order[i]][order[i + 1]] for i in range(len(order) - 1))

    # ── Prepend user location if provided ────────
    user_stop = None
    if user_location:
        user_stop = {
            "name":     "📍 Your Location",
            "address":  "Current GPS position",
            "lat":      user_location["lat"],
            "lon":      user_location["lon"],
        }
        # Add leg from user → warehouse to total distance
        user_coord = (user_location["lat"], user_location["lon"])
        leg0_dist, _ = osrm_client.get_road_distance(
            user_coord[0], user_coord[1],
            ordered_coords[0][0], ordered_coords[0][1]
        )
        total_m += leg0_dist
        full_coords = [user_coord] + ordered_coords
    else:
        full_coords = ordered_coords

    # Road polyline via OSRM CH (covers full sequence incl. user pos if set)
    road = osrm_client.get_road_polyline(full_coords)

    # ── Build stop list ───────────────────────────
    stops = []
    offset = 0
    if user_stop:
        stops.append({
            "index":       0,
            "name":        user_stop["name"],
            "address":     user_stop["address"],
            "lat":         user_stop["lat"],
            "lon":         user_stop["lon"],
            "is_start":    False,
            "is_user_pos": True,
        })
        offset = 1

    for i, r in enumerate(ordered):
        stops.append({
            "index":       i + offset,
            "name":        r["name"],
            "address":     r["address"],
            "lat":         r["lat"],
            "lon":         r["lon"],
            "is_start":    i == 0,      # warehouse is always is_start
            "is_user_pos": False,
        })

    return {
        "stops":             stops,
        "polyline":          road["polyline"],
        "total_distance_m":  total_m,
        "total_distance_km": round(total_m / 1000, 2),
        "total_duration_s":  road["duration_s"],
        "legs":              road["legs"],
        "has_user_location": user_location is not None,
    }


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/suggest")
def suggest_endpoint():
    """
    Three-layer autocomplete (fastest to slowest):
      Layer 1 — Trie lookup    : O(k), 0ms, zero API calls
      Layer 2 — Photon API     : free, ~100ms, results inserted into Trie
    Mapbox is NOT called here — it is reserved for the /geocode fallback.

    Query params:
      q   : search text (required, min 3 chars)
      lat : map centre latitude  (optional, for Photon proximity biasing)
      lon : map centre longitude (optional, for Photon proximity biasing)
    """
    q = request.args.get("q", "").strip()
    if len(q) < 3:
        return jsonify([])

    # ── Layer 1: Trie (instant, in-memory) ──────────────────────────────
    trie_hits = _location_trie.search(q, limit=6)
    if len(trie_hits) >= 3:
        # Enough high-confidence results from cache — skip the network entirely
        return jsonify(trie_hits)

    # ── Layer 2: Photon API fallback ─────────────────────────────────────
    try:
        url    = "https://photon.komoot.io/api/"
        params = {
            "q":     q,
            "limit": 6,
            "lang":  "en",
        }
        try:
            clat = float(request.args.get("lat", ""))
            clon = float(request.args.get("lon", ""))
            params["lat"] = clat
            params["lon"] = clon
        except (TypeError, ValueError):
            pass

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, */*",
        }
        resp = _requests.get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()   # surface non-200 as an explicit error
        data = resp.json()

        suggestions = []
        for feature in data.get("features", []):
            coords = feature.get("geometry", {}).get("coordinates", [None, None])
            lon, lat = coords[0], coords[1]
            if lat is None or lon is None:
                continue

            props   = feature.get("properties", {})
            name    = props.get("name", "")
            city    = props.get("city") or props.get("town") or props.get("village") or ""
            state   = props.get("state", "")
            country = props.get("country", "")

            context = city or state
            short   = f"{name}, {context}" if context and name.lower() != context.lower() else name
            display = ", ".join(dict.fromkeys(p for p in [name, city, state, country] if p))

            payload = {
                "display_name": display,
                "short_name":   short,
                "lat":          lat,
                "lon":          lon,
            }
            suggestions.append(payload)

            # Insert into Trie so next time this query is instant
            _location_trie.insert(q,     payload)     # raw query → result
            _location_trie.insert(short, payload)     # short label → result

        return jsonify(suggestions)
    except Exception as e:
        app.logger.error(f"Photon suggest error: {e}")
        return jsonify([])



@app.route("/geocode", methods=["POST"])
def geocode_endpoint():
    data = request.get_json()
    place = (data or {}).get("place", "").strip()
    if not place:
        return jsonify({"error": "No place provided"}), 400
    try:
        result = geocode_place(place)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Geocoding failed: {e}"}), 500


@app.route("/route", methods=["POST"])
def route_endpoint():
    """
    Compute optimal route.

    Body:
    {
      "warehouse":      string,
      "stops":          [string, ...],
      "pre_resolved":   [                         // optional — from autocomplete selections
        {"lat": float, "lon": float, "address": str} | null,
        ...                                       // index 0 = warehouse, 1+ = stops
      ],
      "user_location":  {"lat": float, "lon": float}  // optional
    }

    When user_location is provided the full route becomes:
      [You] → Warehouse → optimised stops

    Pre-resolved entries bypass Nominatim geocoding, preventing re-geocoding
    to a different location than what was shown in autocomplete.
    """
    data          = request.get_json() or {}
    warehouse     = data.get("warehouse", "").strip()
    stops         = data.get("stops", [])
    pre_resolved  = data.get("pre_resolved", [])  # list parallel to [warehouse]+stops
    user_location = data.get("user_location")

    if not warehouse:
        return jsonify({"error": "Warehouse location required"}), 400
    if not stops:
        return jsonify({"error": "At least one delivery stop required"}), 400

    places = [warehouse] + [s.strip() for s in stops if s.strip()]
    try:
        result = compute_route(places,
                               user_location=user_location,
                               pre_resolved=pre_resolved)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Route computation failed: {e}"}), 500


@app.route("/reroute", methods=["POST"])
def reroute_endpoint():
    """
    Reroute when a stop is added mid-journey.
    Accepts the same payload as /route — the entire updated stop list.
    """
    return route_endpoint()



@app.route("/snap_route", methods=["POST"])
def snap_route_endpoint():
    """
    Update polyline from current GPS position to remaining stops.
    Called when user deviates from the planned polyline.

    Body: {
      "user_lat": float,
      "user_lon": float,
      "remaining_stops": [{"lat": float, "lon": float}, ...]
    }
    """
    data = request.get_json()
    user_lat  = data.get("user_lat")
    user_lon  = data.get("user_lon")
    remaining = data.get("remaining_stops", [])

    if user_lat is None or user_lon is None:
        return jsonify({"error": "user_lat and user_lon required"}), 400

    remaining_coords = [(s["lat"], s["lon"]) for s in remaining]
    try:
        result = osrm_client.snap_to_route(user_lat, user_lon, remaining_coords)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Snap failed: {e}"}), 500


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 54)
    print("  Route Optimizer — Google Maps Style UI")
    print("  Geocoder : Mapbox (30-day TTL cache)")
    print("  Suggest  : Trie → Photon fallback")
    print("=" * 54 + "\n")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
