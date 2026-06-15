/**
 * app.js — Route Optimizer Frontend
 * Google Maps-style UI with GPS navigation, dynamic rerouting,
 * and Contraction Hierarchies (OSRM) powered routing.
 */

"use strict";

// ═══════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════
const State = {
  route:             null,
  markers:           {},
  polylines:         [],
  gpsMarker:         null,
  navActive:         false,
  simInterval:       null,
  simIndex:          0,
  darkTiles:         false,
  currentStopIdx:    0,
  completedStops:    new Set(),
  warehouseGeo:      null,
  routeMode:         "warehouse_then_stops",
};

// ═══════════════════════════════════════════════════
// MAP INIT
// ═══════════════════════════════════════════════════
const map = L.map("map", {
  center: [20.5937, 78.9629],   // India centre default
  zoom: 5,
  zoomControl: false,
});

L.control.zoom({ position: "bottomright" }).addTo(map);

const TILES = {
  light: L.tileLayer(
    "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap contributors", maxZoom: 19 }
  ),
  dark: L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { attribution: "© OpenStreetMap © CARTO", maxZoom: 19 }
  ),
};
TILES.dark.addTo(map);
State.darkTiles = true;

// ═══════════════════════════════════════════════════
// DOM REFS
// ═══════════════════════════════════════════════════
const $ = id => document.getElementById(id);
const warehouseInput   = $("warehouse-input");
const warehouseGeoBtn  = $("warehouse-geo-btn");
const warehouseResult  = $("warehouse-result");
const warehouseAcList  = $("ac-warehouse");
const stopsContainer   = $("stops-container");
const addStopBtn       = $("add-stop-btn");
const optimizeBtn      = $("optimize-btn");
const clearBtn         = $("clear-btn");

const sidebarEl          = $("sidebar");
const collapseSidebarBtn = $("collapse-sidebar-btn");
const expandSidebarBtn   = $("expand-sidebar-btn");

const inputPanel    = $("input-panel");
const resultsPanel  = $("results-panel");
const loadingEl     = $("loading");
const loadingText   = $("loading-text");

const statStops  = $("stat-stops");
const statDist   = $("stat-dist");
const statTime   = $("stat-time");
const stopList   = $("stop-list");

const addMidRouteBtn = $("add-mid-route-btn");
const navBtn         = $("nav-btn");

const navBar       = $("nav-bar");
const navNextName  = $("nav-next-name");
const navDistVal   = $("nav-dist-val");
const navSpeedVal  = $("nav-speed-val");
const simBtn       = $("sim-btn");
const stopNavBtn   = $("stop-nav-btn");

const fitBtn            = $("fit-btn");
const toggleDarkBtn     = $("toggle-dark-btn");
const toastEl           = $("toast");
const useMyLocToggle    = $("use-my-location-toggle");
const myLocBanner       = $("my-location-banner");
const myLocSub          = $("my-loc-sub");

// Modal elements
const routeModeModal       = $("route-mode-modal");
const routeModeConfirmBtn  = $("route-mode-confirm");
const routeModeCancelBtn   = $("route-mode-cancel");

// ═══════════════════════════════════════════════════
// TOAST
// ═══════════════════════════════════════════════════
let toastTimer;
function showToast(msg, isError = false) {
  clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.className = "toast" + (isError ? " error" : "");
  toastEl.classList.remove("hidden");
  toastTimer = setTimeout(() => toastEl.classList.add("hidden"), 3200);
}

// ═══════════════════════════════════════════════════
// LOADING
// ═══════════════════════════════════════════════════
function setLoading(on, text = "Computing optimal route…") {
  loadingText.textContent = text;
  loadingEl.classList.toggle("hidden", !on);
}

// ═══════════════════════════════════════════════════
// STOP INPUT MANAGEMENT
// ═══════════════════════════════════════════════════
let stopCount = 0;

// Extend state with userPosMarker
State.userPosMarker = null;

function addStopRow(value = "") {
  stopCount++;
  const idx = stopCount;
  const row = document.createElement("div");
  row.className = "stop-row";
  row.dataset.stopId = idx;

  // Inner row with number + input + remove button
  const inner = document.createElement("div");
  inner.className = "stop-row-inner";
  inner.innerHTML = `
    <div class="stop-num">${getStopCount() + 1}</div>
    <div class="stop-reorder-actions">
      <button class="reorder-btn move-up-btn" title="Move Up">▲</button>
      <button class="reorder-btn move-down-btn" title="Move Down">▼</button>
    </div>
    <div class="ac-wrapper" style="flex:1">
      <input class="stop-input" type="text" placeholder="e.g. Shimla, HP"
             value="${value}" autocomplete="off" id="stop-input-${idx}" />
      <ul class="ac-dropdown" id="ac-stop-${idx}"></ul>
    </div>
    <button class="remove-stop-btn" title="Remove">✕</button>
  `;
  row.appendChild(inner);

  inner.querySelector(".remove-stop-btn").addEventListener("click", () => {
    row.remove();
    renumberStops();
    updateOptimizeState();
  });

  const moveUpBtn = inner.querySelector(".move-up-btn");
  const moveDownBtn = inner.querySelector(".move-down-btn");

  moveUpBtn.addEventListener("click", () => {
    const prev = row.previousElementSibling;
    if (prev && prev.classList.contains("stop-row")) {
      stopsContainer.insertBefore(row, prev);
      renumberStops();
      updateOptimizeState();
    }
  });

  moveDownBtn.addEventListener("click", () => {
    const next = row.nextElementSibling;
    if (next && next.classList.contains("stop-row")) {
      stopsContainer.insertBefore(row, next.nextSibling);
      renumberStops();
      updateOptimizeState();
    }
  });

  const stopInput = inner.querySelector(".stop-input");
  const stopAcList = inner.querySelector(".ac-dropdown");

  attachAutocomplete(stopInput, stopAcList, item => {
    map.flyTo([item.lat, item.lon], 13, { duration: 1 });
  });

  stopInput.addEventListener("input", updateOptimizeState);
  stopsContainer.appendChild(row);
  stopInput.focus();
  updateOptimizeState();
  renumberStops();
}

function getStopCount() {
  return stopsContainer.querySelectorAll(".stop-row").length;
}

function renumberStops() {
  const rows = [...stopsContainer.querySelectorAll(".stop-row")];
  rows.forEach((row, i) => {
    const numEl = row.querySelector(".stop-num");
    if (numEl) numEl.textContent = i + 1;

    const moveUpBtn = row.querySelector(".move-up-btn");
    const moveDownBtn = row.querySelector(".move-down-btn");
    if (moveUpBtn) moveUpBtn.disabled = (i === 0);
    if (moveDownBtn) moveDownBtn.disabled = (i === rows.length - 1);
  });
}

function getStopValues() {
  return [...stopsContainer.querySelectorAll(".stop-input")]
    .map(i => i.value.trim())
    .filter(Boolean);
}

function updateOptimizeState() {
  const hasWarehouse = warehouseInput.value.trim().length > 0;
  const hasStops = getStopValues().length > 0;
  optimizeBtn.disabled = !(hasWarehouse && hasStops);
}

addStopBtn.addEventListener("click", () => addStopRow());
warehouseInput.addEventListener("input", updateOptimizeState);

// Start with one empty stop
addStopRow();

// ═══════════════════════════════════════════════════
// AUTOCOMPLETE ENGINE
// ═══════════════════════════════════════════════════

let acDebounceTimer;

/**
 * WeakMap: HTMLInputElement → {lat, lon, address} from the last autocomplete selection.
 * Cleared when the user edits the input text after a selection.
 */
const resolvedCoords = new WeakMap();

/**
 * Attach Google-Maps-style autocomplete to any input + dropdown ul pair.
 * @param {HTMLInputElement} inputEl  - the text input
 * @param {HTMLUListElement} listEl   - the <ul class="ac-dropdown"> to populate
 * @param {Function} onSelect        - called with {short_name, display_name, lat, lon}
 */
function attachAutocomplete(inputEl, listEl, onSelect) {
  let suggestions = [];
  let activeIdx   = -1;

  function clearList() {
    listEl.innerHTML = "";
    suggestions = [];
    activeIdx = -1;
  }

  function renderList(items) {
    clearList();
    suggestions = items;
    items.forEach((item, i) => {
      const li = document.createElement("li");
      li.className = "ac-item";
      li.innerHTML = `
        <span class="ac-pin">📍</span>
        <div class="ac-text">
          <div class="ac-short">${escHtml(item.short_name)}</div>
          <div class="ac-full">${escHtml(item.display_name)}</div>
        </div>`;
      li.addEventListener("mousedown", e => {
        e.preventDefault();   // don't blur input before click fires
        choose(i);
      });
      listEl.appendChild(li);
    });
  }

  function setActive(idx) {
    listEl.querySelectorAll(".ac-item").forEach((el, i) =>
      el.classList.toggle("ac-active", i === idx)
    );
    activeIdx = idx;
  }

  function choose(idx) {
    const item = suggestions[idx];
    if (!item) return;
    inputEl.value = item.short_name;
    clearList();
    // ★ Store resolved coords so computeRoute can bypass re-geocoding
    resolvedCoords.set(inputEl, {
      lat:     item.lat,
      lon:     item.lon,
      address: item.display_name,
    });
    onSelect(item);
    updateOptimizeState();
  }

  inputEl.addEventListener("input", () => {
    // Clear any previously resolved coords when the user edits the text
    resolvedCoords.delete(inputEl);
    updateOptimizeState();
    clearTimeout(acDebounceTimer);
    const q = inputEl.value.trim();
    if (q.length < 3) { clearList(); return; }
    acDebounceTimer = setTimeout(async () => {
      try {
        // Include map centre so Nominatim biases results geographically
        const c    = map.getCenter();
        const zoom = map.getZoom();
        const url  = `/suggest?q=${encodeURIComponent(q)}&lat=${c.lat}&lon=${c.lng}&zoom=${zoom}`;
        const res  = await fetch(url);
        const items = await res.json();
        if (items.length > 0) renderList(items);
        else clearList();
      } catch { clearList(); }
    }, 350);   // 350ms debounce
  });

  inputEl.addEventListener("keydown", e => {
    if (!suggestions.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(Math.min(activeIdx + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(Math.max(activeIdx - 1, 0));
    } else if (e.key === "Enter" && activeIdx >= 0) {
      e.preventDefault();
      choose(activeIdx);
    } else if (e.key === "Escape") {
      clearList();
    }
  });

  inputEl.addEventListener("blur", () => {
    // Small delay so mousedown on list item fires first
    setTimeout(clearList, 180);
  });
}

function escHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// Attach autocomplete to warehouse input
attachAutocomplete(warehouseInput, warehouseAcList, item => {
  warehouseResult.textContent = `✅ ${item.display_name.slice(0, 70)}`;
  warehouseResult.className = "geo-result";
  map.flyTo([item.lat, item.lon], 13, { duration: 1 });
  // resolvedCoords is set inside attachAutocomplete's choose() — no need to set here
});

// ═══════════════════════════════════════════════════
// MY LOCATION TOGGLE
// ═══════════════════════════════════════════════════
let userGPSPosition = null;   // {lat, lon} captured when toggle is on

useMyLocToggle.addEventListener("change", () => {
  if (useMyLocToggle.checked) {
    if (!navigator.geolocation) {
      showToast("Geolocation not supported by your browser", true);
      useMyLocToggle.checked = false;
      return;
    }
    myLocSub.textContent = "Getting your position…";
    navigator.geolocation.getCurrentPosition(
      pos => {
        userGPSPosition = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        // Show modal to pick route mode
        showRouteModeModal();
        showUserPositionMarker(userGPSPosition.lat, userGPSPosition.lon);
      },
      () => {
        useMyLocToggle.checked = false;
        myLocSub.textContent = "Route: Warehouse → Stops";
        showToast("Could not get GPS position", true);
      },
      { enableHighAccuracy: true, timeout: 8000 }
    );
  } else {
    userGPSPosition = null;
    State.routeMode = "warehouse_then_stops";
    myLocBanner.classList.remove("active");
    myLocSub.textContent = "Route: Warehouse → Stops";
    if (State.userPosMarker) {
      map.removeLayer(State.userPosMarker);
      State.userPosMarker = null;
    }
    updateToWarehouseBtn();
  }
});

// ═══════════════════════════════════════════════════
// ROUTE MODE MODAL
// ═══════════════════════════════════════════════════
function showRouteModeModal() {
  // Reset radio to default
  const radios = routeModeModal.querySelectorAll('input[name="route-mode"]');
  radios.forEach(r => { r.checked = r.value === "warehouse_then_stops"; });
  routeModeModal.classList.remove("hidden");
}

routeModeConfirmBtn.addEventListener("click", () => {
  const selected = routeModeModal.querySelector('input[name="route-mode"]:checked');
  State.routeMode = selected ? selected.value : "warehouse_then_stops";
  routeModeModal.classList.add("hidden");

  myLocBanner.classList.add("active");
  if (State.routeMode === "warehouse_then_stops") {
    myLocSub.textContent = "Route: You → Warehouse → Stops";
  } else {
    myLocSub.textContent = "Route: You → Stops (skip warehouse)";
  }
  showToast("📍 GPS captured — mode: " + (State.routeMode === "warehouse_then_stops" ? "Warehouse + Stops" : "Stops Only"));
  updateNavBtn();
  updateToWarehouseBtn();
});

routeModeCancelBtn.addEventListener("click", () => {
  routeModeModal.classList.add("hidden");
  useMyLocToggle.checked = false;
  userGPSPosition = null;
  myLocSub.textContent = "Route: Warehouse → Stops";
  if (State.userPosMarker) {
    map.removeLayer(State.userPosMarker);
    State.userPosMarker = null;
  }
  updateNavBtn();
  updateToWarehouseBtn();
});

function showUserPositionMarker(lat, lon) {
  if (State.userPosMarker) map.removeLayer(State.userPosMarker);
  const icon = L.divIcon({
    html: `<div class="marker-user-pos" style="width:48px;height:26px;">YOU</div>`,
    className: "", iconSize: [48, 26], iconAnchor: [24, 13]
  });
  State.userPosMarker = L.marker([lat, lon], { icon })
    .bindPopup(`<b>📍 Your Current Location</b>`)
    .addTo(map);
  map.setView([lat, lon], 13);
}

// ═══════════════════════════════════════════════════
// API CALLS
// ═══════════════════════════════════════════════════
async function apiPost(endpoint, body) {
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ═══════════════════════════════════════════════════
// OPTIMIZE ROUTE
// ═══════════════════════════════════════════════════
optimizeBtn.addEventListener("click", () => computeRoute("/route"));
addMidRouteBtn.addEventListener("click", () => {
  // Show input panel again to add stop
  inputPanel.classList.remove("hidden");
  resultsPanel.classList.add("hidden");
  addStopRow();
  showToast("Add your new stop, then click Optimize Route");
});

async function computeRoute(endpoint = "/route") {
  const warehouse = warehouseInput.value.trim();
  const stops     = getStopValues();

  const gpsOn   = useMyLocToggle.checked && userGPSPosition;
  const stopsOnly = gpsOn && State.routeMode === "stops_only";

  // In stops_only mode the warehouse field is not part of the route
  if (!stopsOnly && !warehouse) return;
  if (stops.length === 0) return;

  const stopInputEls = [...stopsContainer.querySelectorAll(".stop-input")];

  let body;
  if (stopsOnly) {
    // Route: GPS → stops (no warehouse in places list)
    const preResolved = stopInputEls.map(el => resolvedCoords.get(el) || null);
    body = {
      warehouse: stops[0],          // backend needs a "warehouse" field; use first stop as anchor
      stops:     stops.slice(1).length ? stops.slice(1) : stops,
      pre_resolved: [null, ...preResolved.slice(1)],
      user_location: userGPSPosition,
      route_mode: "stops_only",
    };
    // Simpler: send all stops as the list and a dummy warehouse = first stop
    // Actually, cleanest: send warehouse = stops[0] so TSP matrix covers all stops
    body = {
      warehouse:    stops[0],
      stops:        stops.length > 1 ? stops.slice(1) : [stops[0]],
      pre_resolved: [preResolved[0] || null, ...preResolved.slice(1)],
      user_location: userGPSPosition,
      skip_warehouse_marker: true,
    };
  } else {
    const preResolved = [
      resolvedCoords.get(warehouseInput) || null,
      ...stopInputEls.map(el => resolvedCoords.get(el) || null)
    ];
    body = { warehouse, stops, pre_resolved: preResolved };
    if (gpsOn) body.user_location = userGPSPosition;
  }

  // Track warehouse resolved coords for "To Warehouse" feature
  State.warehouseResolved = resolvedCoords.get(warehouseInput) || null;

  setLoading(true, "Running Contraction Hierarchies + GA TSP…");
  try {
    const data = await apiPost(endpoint, body);
    State.route = data;
    State.completedStops.clear();
    State.currentStopIdx = data.has_user_location ? 1 : 0;
    renderRoute(data);
    showResultsPanel(data);
    showToast(`✅ Route optimised — ${data.total_distance_km} km`);
  } catch (err) {
    showToast(`Error: ${err.message}`, true);
  } finally {
    setLoading(false);
  }
}

navBtn.addEventListener("click", startNavigation);


// ═══════════════════════════════════════════════════
// RENDER ROUTE ON MAP
// ═══════════════════════════════════════════════════
function clearMap() {
  State.polylines.forEach(l => map.removeLayer(l));
  State.polylines = [];
  Object.values(State.markers).forEach(m => map.removeLayer(m));
  State.markers = {};
  if (State.gpsMarker) { map.removeLayer(State.gpsMarker); State.gpsMarker = null; }
}

function renderRoute(data) {
  clearMap();

  // Draw polyline
  if (data.polyline && data.polyline.length > 1) {
    const line = L.polyline(data.polyline, {
      color: "#3b7bff",
      weight: 6,
      opacity: 0.9,
      smoothFactor: 1.2,
    }).addTo(map);
    State.polylines.push(line);
    map.fitBounds(line.getBounds(), { padding: [50, 50] });
  }

  // Place markers
  data.stops.forEach(stop => placeMarker(stop));
}

function placeMarker(stop) {
  let iconHtml, size, anchor;

  if (stop.is_user_pos) {
    // Green "YOU" banner — current user position
    iconHtml = `<div class="marker-user-pos" style="width:48px;height:26px;">YOU</div>`;
    size = [48, 26]; anchor = [24, 13];
  } else if (stop.is_start) {
    iconHtml = `<div class="marker-start" style="width:52px;height:28px;">START</div>`;
    size = [52, 28]; anchor = [26, 14];
  } else {
    const done = State.completedStops.has(stop.index);
    iconHtml = `<div class="marker-stop${done ? " completed" : ""}" style="width:30px;height:30px;">${stop.index}</div>`;
    size = [30, 30]; anchor = [15, 15];
  }

  const icon = L.divIcon({ html: iconHtml, className: "", iconSize: size, iconAnchor: anchor });
  const popupLabel = stop.is_user_pos
    ? "📍 Your Current Location"
    : stop.is_start ? "🏭 Warehouse" : "📦 Stop " + stop.index;

  const marker = L.marker([stop.lat, stop.lon], { icon })
    .bindPopup(`<b style="font-family:Inter,sans-serif">${popupLabel}</b><br>
      <span style="font-size:12px;color:#555">${stop.address || stop.name}</span>`)
    .addTo(map);

  State.markers[stop.index] = marker;
}

// ═══════════════════════════════════════════════════
// RESULTS SIDEBAR
// ═══════════════════════════════════════════════════
function showResultsPanel(data) {
  inputPanel.classList.add("hidden");
  resultsPanel.classList.remove("hidden");

  statStops.textContent = data.stops.length;
  statDist.textContent  = data.total_distance_km;
  const hrs = data.total_duration_s ? (data.total_duration_s / 3600).toFixed(1) : "—";
  statTime.textContent  = hrs;

  stopList.innerHTML = "";
  
  const container = document.createElement("div");
  container.className = "stop-list-container";

  data.stops.forEach((stop, i) => {
    // If not the first stop, show leg distance and duration details in the timeline
    if (i > 0 && data.legs && data.legs[i - 1]) {
      const leg = data.legs[i - 1];
      const legKm = (leg.distance_m / 1000).toFixed(1);
      const legMins = Math.round(leg.duration_s / 60);
      const legDiv = document.createElement("div");
      legDiv.className = "leg-info";
      legDiv.innerHTML = `
        <div class="leg-info-pill" title="Travel from previous stop">
          🚗 ${legKm} km · ${legMins} min
        </div>
      `;
      container.appendChild(legDiv);
    }

    const div = document.createElement("div");
    div.className = "stop-item";
    div.id = `result-stop-${stop.index}`;

    let badge;
    if (stop.is_user_pos) {
      badge = `<div class="stop-badge" style="background:var(--green);width:36px;font-size:9px">YOU</div>`;
    } else if (stop.is_start) {
      badge = `<div class="stop-badge is-start">START</div>`;
    } else {
      badge = `<div class="stop-badge${State.completedStops.has(stop.index) ? " completed" : ""}">${stop.index}</div>`;
    }

    div.innerHTML = `
      ${badge}
      <div class="stop-details">
        <div class="stop-name">${stop.name}</div>
        <div class="stop-addr">${(stop.address || "").split(",").slice(0, 2).join(",")}</div>
      </div>
    `;

    // Click: Center map on marker and open popup
    div.addEventListener("click", () => {
      map.flyTo([stop.lat, stop.lon], 14, { duration: 1.2 });
      State.markers[stop.index]?.openPopup();
    });

    // Hover: Highlight list item and bounce/pulse the map marker
    div.addEventListener("mouseenter", () => {
      div.classList.add("active");
      const marker = State.markers[stop.index];
      if (marker) {
        marker.openPopup();
        const markerEl = marker.getElement();
        if (markerEl) {
          markerEl.querySelector(".marker-stop, .marker-start, .marker-user-pos")?.classList.add("hover-highlight");
        }
      }
    });

    div.addEventListener("mouseleave", () => {
      div.classList.remove("active");
      const marker = State.markers[stop.index];
      if (marker) {
        marker.closePopup();
        const markerEl = marker.getElement();
        if (markerEl) {
          markerEl.querySelector(".marker-stop, .marker-start, .marker-user-pos")?.classList.remove("hover-highlight");
        }
      }
    });

    container.appendChild(div);
  });
  
  stopList.appendChild(container);
}

// ═══════════════════════════════════════════════════
// MAP CONTROLS
// ═══════════════════════════════════════════════════
fitBtn.addEventListener("click", () => {
  if (State.polylines.length > 0) {
    map.fitBounds(State.polylines[0].getBounds(), { padding: [50, 50] });
  }
});

toggleDarkBtn.addEventListener("click", () => {
  if (State.darkTiles) {
    map.removeLayer(TILES.dark);
    TILES.light.addTo(map);
  } else {
    map.removeLayer(TILES.light);
    TILES.dark.addTo(map);
  }
  State.darkTiles = !State.darkTiles;
});

// ═══════════════════════════════════════════════════
// SIDEBAR COLLAPSE / EXPAND
// ═══════════════════════════════════════════════════
collapseSidebarBtn.addEventListener("click", () => {
  sidebarEl.classList.add("collapsed");
  expandSidebarBtn.classList.remove("hidden");
  setTimeout(() => map.invalidateSize(), 300);
});

expandSidebarBtn.addEventListener("click", () => {
  sidebarEl.classList.remove("collapsed");
  expandSidebarBtn.classList.add("hidden");
  setTimeout(() => map.invalidateSize(), 300);
});

// ═══════════════════════════════════════════════════
// CLEAR
// ═══════════════════════════════════════════════════
clearBtn.addEventListener("click", () => {
  stopNavigation();
  clearMap();
  // Also clear any to-warehouse overlays
  State.toWarehousePolylines.forEach(l => map.removeLayer(l));
  State.toWarehousePolylines = [];
  stopsContainer.innerHTML = "";
  stopCount = 0;
  warehouseInput.value = "";
  warehouseResult.className = "geo-result hidden";
  resultsPanel.classList.add("hidden");
  inputPanel.classList.remove("hidden");
  State.route = null;
  State.warehouseResolved = null;
  addStopRow();
  updateOptimizeState();
  updateNavBtn();
  updateToWarehouseBtn();
});

// ═══════════════════════════════════════════════════
// GPS NAVIGATION
// ═══════════════════════════════════════════════════

let watchId = null;
let lastPosition = null;
let lastPositionTime = null;
const DEVIATION_THRESHOLD_M = 60;  // reroute if >60m off polyline

// navBtn click is handled dynamically by updateNavBtn() above
stopNavBtn.addEventListener("click", stopNavigation);

function startNavigation() {
  if (!State.route) return;
  State.navActive = true;
  State.currentStopIdx = 1;   // Start heading to stop 1 (past warehouse)
  navBar.classList.remove("hidden");
  updateNavBar();
  showToast("📍 Navigation started");

  if (navigator.geolocation) {
    watchId = navigator.geolocation.watchPosition(
      onGpsUpdate,
      () => showToast("GPS unavailable — use Simulate mode", true),
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 10000 }
    );
  }
}

function stopNavigation() {
  State.navActive = false;
  State.simIndex = 0;
  if (watchId !== null) { navigator.geolocation.clearWatch(watchId); watchId = null; }
  if (State.simInterval) { clearInterval(State.simInterval); State.simInterval = null; }
  if (State.gpsMarker) { map.removeLayer(State.gpsMarker); State.gpsMarker = null; }
  navBar.classList.add("hidden");
  // Restore all polylines to blue
  State.polylines.forEach(l => l.setStyle({ color: "#3b7bff", opacity: 0.9 }));
  simBtn.textContent = "▶ Simulate";
}

// ── Real GPS update ──────────────────────────────
function onGpsUpdate(pos) {
  const lat = pos.coords.latitude;
  const lon = pos.coords.longitude;
  const speed = pos.coords.speed || 0;

  const now = Date.now();
  if (lastPosition && lastPositionTime) {
    const dt = (now - lastPositionTime) / 1000;
    const ds = haversineJS(lastPosition[0], lastPosition[1], lat, lon);
    const kmh = (ds / 1000) / (dt / 3600);
    navSpeedVal.textContent = kmh.toFixed(0);
  }
  lastPosition = [lat, lon];
  lastPositionTime = now;

  moveGpsMarker(lat, lon);
  checkStopArrival(lat, lon);
  checkDeviation(lat, lon);
}

// ── GPS marker ───────────────────────────────────
function moveGpsMarker(lat, lon) {
  if (!State.gpsMarker) {
    const icon = L.divIcon({
      html: `<div class="marker-gps" style="width:18px;height:18px;"></div>`,
      className: "",
      iconSize: [18, 18],
      iconAnchor: [9, 9],
    });
    State.gpsMarker = L.marker([lat, lon], { icon, zIndexOffset: 1000 }).addTo(map);
  } else {
    State.gpsMarker.setLatLng([lat, lon]);
  }
  map.panTo([lat, lon], { animate: true, duration: 0.5 });
  updateNavBar(lat, lon);
}

// ── Check if arrived at next stop ────────────────
function checkStopArrival(lat, lon) {
  if (!State.route) return;
  const stops = State.route.stops;
  if (State.currentStopIdx >= stops.length) return;

  const next = stops[State.currentStopIdx];
  const dist = haversineJS(lat, lon, next.lat, next.lon);

  if (dist < 80) {  // within 80m → arrived
    State.completedStops.add(next.index);
    showToast(`✅ Arrived at ${next.name}`);

    // Gray out marker
    const el = State.markers[next.index]?.getElement();
    if (el) el.querySelector(".marker-stop")?.classList.add("completed");

    // Gray out in sidebar
    const sideItem = document.getElementById(`result-stop-${next.index}`);
    if (sideItem) sideItem.querySelector(".stop-badge")?.classList.add("completed");

    State.currentStopIdx++;
    if (State.currentStopIdx >= stops.length) {
      showToast("🎉 All stops completed!", false);
      stopNavigation();
    } else {
      updateNavBar(lat, lon);
    }
  }
}

// ── Check deviation from polyline ────────────────
async function checkDeviation(lat, lon) {
  if (!State.route || !State.route.polyline) return;
  const dist = distToPolyline(lat, lon, State.route.polyline);
  if (dist > DEVIATION_THRESHOLD_M) {
    showToast("🔄 Off route — recalculating…");
    const remainingStops = State.route.stops.slice(State.currentStopIdx).map(s => ({ lat: s.lat, lon: s.lon }));
    try {
      const snapped = await apiPost("/snap_route", {
        user_lat: lat, user_lon: lon, remaining_stops: remainingStops
      });
      // Replace polyline
      State.polylines.forEach(l => map.removeLayer(l));
      State.polylines = [];
      if (snapped.polyline && snapped.polyline.length > 1) {
        const line = L.polyline(snapped.polyline, { color: "#3b7bff", weight: 6, opacity: 0.9 }).addTo(map);
        State.polylines.push(line);
        State.route.polyline = snapped.polyline;
      }
    } catch (e) { /* silently ignore */ }
  }
}

// ── Update nav bar display ───────────────────────
function updateNavBar(userLat, userLon) {
  if (!State.route) return;
  const stops = State.route.stops;
  if (State.currentStopIdx >= stops.length) return;
  const next = stops[State.currentStopIdx];
  navNextName.textContent = next.name;

  if (userLat !== undefined && userLon !== undefined) {
    const dist = haversineJS(userLat, userLon, next.lat, next.lon);
    navDistVal.textContent = (dist / 1000).toFixed(1);
  } else {
    navDistVal.textContent = "—";
    navSpeedVal.textContent = "—";
  }
}

// ═══════════════════════════════════════════════════
// SIMULATION MODE
// ═══════════════════════════════════════════════════

simBtn.addEventListener("click", () => {
  if (State.simInterval) {
    clearInterval(State.simInterval);
    State.simInterval = null;
    simBtn.textContent = "▶ Simulate";
    return;
  }
  if (!State.route || !State.route.polyline || State.route.polyline.length < 2) {
    showToast("No route to simulate", true);
    return;
  }
  if (!State.navActive) startNavigation();

  State.simIndex = 0;
  simBtn.textContent = "⏸ Pause";
  const polyline = State.route.polyline;

  State.simInterval = setInterval(() => {
    if (State.simIndex >= polyline.length) {
      clearInterval(State.simInterval);
      State.simInterval = null;
      simBtn.textContent = "▶ Simulate";
      showToast("🏁 Simulation complete");
      return;
    }
    const [lat, lon] = polyline[State.simIndex];
    moveGpsMarker(lat, lon);
    checkStopArrival(lat, lon);

    // Fake speed
    if (State.simIndex > 0) {
      const [plat, plon] = polyline[State.simIndex - 1];
      const ds = haversineJS(plat, plon, lat, lon);
      navSpeedVal.textContent = Math.round(ds * 12);  // ~artificial km/h
    }

    // Draw "traveled" overlay segment in green
    if (State.simIndex > 0 && State.simIndex % 10 === 0) {
      L.polyline(polyline.slice(0, State.simIndex + 1), {
        color: "#22c55e", weight: 5, opacity: 0.7
      }).addTo(map);
    }

    State.simIndex++;
  }, 80);  // ~80ms per point → smooth animation
});

// ═══════════════════════════════════════════════════
// GEOMETRY HELPERS
// ═══════════════════════════════════════════════════

function haversineJS(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 +
            Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) * Math.sin(dLon/2)**2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function distToPolyline(lat, lon, polyline) {
  let minDist = Infinity;
  for (let i = 0; i < polyline.length - 1; i++) {
    const d = pointToSegmentDist(lat, lon,
      polyline[i][0], polyline[i][1],
      polyline[i+1][0], polyline[i+1][1]);
    if (d < minDist) minDist = d;
  }
  return minDist;
}

function pointToSegmentDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  if (dx === 0 && dy === 0) return haversineJS(px, py, ax, ay);
  const t = Math.max(0, Math.min(1, ((px-ax)*dx + (py-ay)*dy) / (dx*dx + dy*dy)));
  return haversineJS(px, py, ax + t*dx, ay + t*dy);
}
