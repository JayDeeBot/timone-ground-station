document.addEventListener('DOMContentLoaded', function() {
  const mapsTab = document.querySelector('#maps-tab');
  if (mapsTab) {
    mapsTab.addEventListener('shown.bs.tab', function () {
      ensureMap();
      initGroundGPSControls();
      refreshMapList();
    });
  } else {
    if (document.getElementById('imageMap')) {
      ensureMap();
      initGroundGPSControls();
      refreshMapList();
    }
  }

  const deleteBtn = document.getElementById('deleteMapBtn');
  if (deleteBtn) {
    deleteBtn.addEventListener('click', async () => {
      const sel = document.getElementById('mapSelect');
      const id = sel?.value || '';
      if (!id) { alert('Select a map to delete.'); return; }
      if (!confirm(`Delete saved map "${id}"? This cannot be undone.`)) return;
      try {
        const res = await fetch(`/api/maps/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(await res.text());
        await refreshMapList('');
      } catch (e) {
        console.error('Delete failed:', e);
        alert('Failed to delete map.');
      }
    });
  }
});

// Global error trap
window.addEventListener('error', (e) => {
  console.error('[Maps] Uncaught error:', e.error || e.message);
});

let leafletMap = null;
let currentOverlay = null;
let groundMarker = null;
let rocketMarker = null;

function ensureMap() {
  if (leafletMap) return;

  leafletMap = L.map('imageMap', {
    center: [-33.86, 151.21],
    zoom: 8,
    attributionControl: false,
    preferCanvas: true,
    worldCopyJump: false,
    wheelPxPerZoomLevel: 180,
    wheelDebounceTime: 80,
    zoomDelta: 0.25,
    zoomSnap: 0.25,
    scrollWheelZoom: true
  });

  injectGroundPulseStyles();
  ensureGroundMarker();
  ensureRocketMarker();

  initMapManagerUI();
}

/* ---- markers ---- */
function injectGroundPulseStyles() {
  if (document.getElementById('pulse-style-ground')) return;
  const css = `
  .pulse-ground { position: relative; }
  .pulse-ground .pg-core {
    width: 12px; height: 12px; border-radius: 50%;
    background: #0d6efd;
    position: absolute; top: 0; left: 0;
    box-shadow: 0 0 6px rgba(13,110,253,0.8);
  }
  .pulse-ground .pg-ring {
    width: 12px; height: 12px; border-radius: 50%;
    position: absolute; top: 0; left: 0;
    animation: pgPulse 1.6s ease-out infinite;
    border: 2px solid rgba(13,110,253,0.6);
  }
  @keyframes pgPulse {
    0%   { transform: scale(1);   opacity: 0.8; }
    70%  { transform: scale(2.4); opacity: 0;   }
    100% { transform: scale(2.4); opacity: 0;   }
  }`;
  const style = document.createElement('style');
  style.id = 'pulse-style-ground';
  style.textContent = css;
  document.head.appendChild(style);
}
function ensureGroundMarker() {
  if (!leafletMap || groundMarker) return;
  const icon = L.divIcon({
    className: 'pulse-ground',
    html: '<div class="pg-core"></div><div class="pg-ring"></div>',
    iconSize: [12, 12],
    iconAnchor: [6, 6]
  });
  groundMarker = L.marker([0, 0], { icon, zIndexOffset: 900, interactive: false });
}
function ensureRocketMarker() {
  if (!leafletMap || rocketMarker) return;
  const icon = L.divIcon({
    className: 'pulse-dot',
    html: '<div class="pulse-core"></div><div class="pulse-ring"></div>',
    iconSize: [20,20],
    iconAnchor: [10,10]
  });
  rocketMarker = L.marker([0, 0], { icon, zIndexOffset: 1000, interactive: false });
}

function getSavedGroundLatLon() {
  const lat = parseFloat(localStorage.getItem('groundLat'));
  const lon = parseFloat(localStorage.getItem('groundLon'));
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { lat, lon };
}
function updateGroundMarkerPosition() {
  if (!leafletMap) return;
  const pos = getSavedGroundLatLon();
  if (!pos) return;
  ensureGroundMarker();
  if (!leafletMap.hasLayer(groundMarker)) groundMarker.addTo(leafletMap);
  groundMarker.setLatLng([pos.lat, pos.lon]);
}

/* ---- validation & geofence ---- */
function parseLonLatPair(s) {
  const parts = (s || "").split(',').map(p => p.trim());
  if (parts.length !== 2) return null;
  const lon = Number(parts[0]);
  const lat = Number(parts[1]);
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  if (lon < -180 || lon > 180 || lat < -90 || lat > 90) return null;
  return { lon, lat };
}
function isWithinGeofence(lon, lat) {
  const AUS  = { minLon: 112.0, maxLon: 154.0, minLat: -44.0, maxLat: -10.0 };
  const USA48= { minLon: -125.0, maxLon: -66.9, minLat: 24.5,  maxLat: 49.5  };
  const AK   = { minLon: -170.0, maxLon: -129.0, minLat: 51.0, maxLat: 72.0  };
  const HI   = { minLon: -161.1, maxLon: -154.4, minLat: 18.8, maxLat: 22.4  };
  const inBox = (b) => lon >= b.minLon && lon <= b.maxLon && lat >= b.minLat && lat <= b.maxLat;
  return inBox(AUS) || inBox(USA48) || inBox(AK) || inBox(HI);
}
function validateTwoCorners(tl, br) {
  if (!(tl.lat > br.lat && tl.lon < br.lon)) return 'Top-Left must be above/left of Bottom-Right.';
  if (!isWithinGeofence(tl.lon, tl.lat) || !isWithinGeofence(br.lon, br.lat)) {
    return 'Corners must be within Australia or USA (incl. Alaska/Hawaii).';
  }
  return null;
}

/* ---- map manager UI ---- */
function initMapManagerUI() {
  const mapSelect  = document.getElementById('mapSelect');
  const uploadForm = document.getElementById('mapUploadForm');
  const uploadBtn  = document.getElementById('uploadSaveBtn');
  const errEl      = document.getElementById('cornerError');
  const tlEl       = document.getElementById('tlInput');
  const brEl       = document.getElementById('brInput');

  // Keep the dropdown working
  if (mapSelect) {
    mapSelect.addEventListener('change', () => {
      const id = mapSelect.value;
      if (!id) { clearOverlayAndBounds(); return; }
      const meta = (window._mapsIndex?.maps || []).find(m => m.id === id);
      if (meta) {
        try { renderSatellite(meta); }
        catch (e) { console.error('Render failed:', e); clearOverlayAndBounds(); }
      }
    });
  }

  // Kill native submit (prevents full-page navigation)
  if (uploadForm) {
    uploadForm.addEventListener('submit', (e) => { e.preventDefault(); e.stopPropagation(); return false; });
  }

  // Prevent Enter key from submitting the form
  [tlEl, brEl].forEach(el => {
    if (!el) return;
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); }
    });
  });

  // Click → upload
  if (uploadBtn) {
    uploadBtn.addEventListener('click', async () => {
      try {
        const fileEl = document.getElementById('mapFile');
        const nameEl = document.getElementById('mapName');

        const file = fileEl?.files?.[0];
        if (!file) { alert('Please choose an image file to upload.'); return; }

        const tl = parseLonLatPair(tlEl?.value);
        const br = parseLonLatPair(brEl?.value);
        if (!tl || !br) { return showCornerError('Please provide TL and BR as "lon,lat" (numbers only).'); }
        const bad = validateTwoCorners(tl, br);
        if (bad) { return showCornerError(bad); }
        hideCornerError();

        const fd = new FormData();
        fd.append('file', file);
        if (nameEl?.value?.trim()) fd.append('name', nameEl.value.trim());
        fd.append('top_left',     `${tl.lon},${tl.lat}`);
        fd.append('bottom_right', `${br.lon},${br.lat}`);

        const res  = await fetch('/api/maps', { method: 'POST', body: fd, redirect: 'follow' });
        const ctyp = res.headers.get('content-type') || '';
        let json   = null;
        if (ctyp.includes('application/json')) json = await res.json();
        else json = { ok: res.ok, note: await res.text().catch(()=>'') };

        if (!res.ok) throw new Error(json?.error || `Upload failed (${res.status})`);

        await refreshMapList(json?.map?.id);
        alert('Map uploaded and saved.');
        uploadForm?.reset?.();
      } catch (err) {
        console.error('Upload error:', err);
        alert('Error: ' + (err?.message || err));
      }
    });
  }

  function showCornerError(msg) {
    if (!errEl) return;
    errEl.textContent = msg;
    errEl.classList.remove('d-none');
  }
  function hideCornerError() {
    if (!errEl) return;
    errEl.textContent = '';
    errEl.classList.add('d-none');
  }
}

function clearOverlayAndBounds() {
  if (currentOverlay) {
    leafletMap.removeLayer(currentOverlay);
    currentOverlay = null;
  }
  try { leafletMap.setMaxBounds(null); } catch (_) {}
}

async function refreshMapList(selectId = null) {
  try {
    const res = await fetch(`/api/maps?ts=${Date.now()}`);
    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
      const txt = await res.text();
      throw new Error(`Unexpected response: ${txt.slice(0, 200)}...`);
    }
    const idx = await res.json();
    const all = Array.isArray(idx.maps) ? idx.maps : [];
    const valid = all.filter(m => isValidCorners(m.corners));

    window._mapsIndex = { maps: valid };
    const mapSelect = document.getElementById('mapSelect');
    if (!mapSelect) return;

    const priorValue = selectId ?? mapSelect.value;
    mapSelect.innerHTML = '<option value="">-- None --</option>';

    valid.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.id;
      mapSelect.appendChild(opt);
    });

    if (priorValue && valid.some(m => m.id === priorValue)) {
      mapSelect.value = priorValue;
      const meta = valid.find(m => m.id === priorValue);
      try { renderSatellite(meta); } catch (e) { console.error('Render failed:', e); clearOverlayAndBounds(); }
    } else if (valid.length > 0) {
      mapSelect.value = valid[0].id;
      try { renderSatellite(valid[0]); } catch (e) { console.error('Render failed:', e); clearOverlayAndBounds(); }
    } else {
      clearOverlayAndBounds();
    }
  } catch (err) {
    console.error('Failed to load maps index:', err);
  }
}

function isValidCorners(c) {
  if (!c) return false;
  const keys = ["top_left", "top_right", "bottom_right", "bottom_left"];
  try {
    for (const k of keys) {
      const v = c[k];
      if (!Array.isArray(v) || v.length !== 2) return false;
      const lon = Number(v[0]);
      const lat = Number(v[1]);
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) return false;
      if (lon < -180 || lon > 180 || lat < -90 || lat > 90) return false;
      if (!isWithinGeofence(lon, lat)) return false;
    }
    const b = cornersToBounds(c);
    return !!b;
  } catch { return false; }
}
function cornersToBounds(c) {
  const lons = [c.top_left[0], c.top_right[0], c.bottom_right[0], c.bottom_left[0]].map(Number);
  const lats = [c.top_left[1], c.top_right[1], c.bottom_right[1], c.bottom_left[1]].map(Number);
  if (lons.some(x => !Number.isFinite(x)) || lats.some(x => !Number.isFinite(x))) return null;
  const west  = Math.min(...lons);
  const east  = Math.max(...lons);
  const south = Math.min(...lats);
  const north = Math.max(...lats);
  if (!(north > south && east > west)) return null;
  return { south, west, north, east };
}

function renderSatellite(meta) {
  if (!leafletMap || !meta || !isValidCorners(meta.corners)) {
    console.warn('Skipping render; invalid corners:', meta);
    clearOverlayAndBounds();
    return;
  }
  const b = cornersToBounds(meta.corners);
  if (!b) { clearOverlayAndBounds(); return; }

  const { south, west, north, east } = b;

  if (currentOverlay) {
    leafletMap.removeLayer(currentOverlay);
    currentOverlay = null;
  }
  try {
    const bounds = L.latLngBounds(L.latLng(south, west), L.latLng(north, east));
    currentOverlay = L.imageOverlay(meta.url, bounds, { opacity: 1.0 }).addTo(leafletMap);
    leafletMap.fitBounds(bounds, { padding: [20, 20] });
    const padded = bounds.pad(0.05);
    leafletMap.setMaxBounds(padded);

    updateGroundMarkerPosition();
    if (rocketMarker && leafletMap.hasLayer(rocketMarker)) rocketMarker.bringToFront();
  } catch (e) {
    console.error('Leaflet failed to apply bounds:', e);
    clearOverlayAndBounds();
  }
}

/* ---------- Ground Station GPS (with marker hook) ---------- */
function initGroundGPSControls() {
  const viewMode = document.getElementById('groundGPSView');
  const editMode = document.getElementById('groundGPSEdit');
  const editButton = document.getElementById('editGroundGPS');
  const cancelButton = document.getElementById('cancelGroundGPS');
  const form = document.getElementById('groundGPSForm');
  const latDisplay = document.getElementById('groundLat');
  const lonDisplay = document.getElementById('groundLon');
  const latInput = document.getElementById('groundLatInput');
  const lonInput = document.getElementById('groundLonInput');

  const savedLat = localStorage.getItem('groundLat');
  const savedLon = localStorage.getItem('groundLon');
  if (savedLat && savedLon) {
    latDisplay.textContent = `${parseFloat(savedLat).toFixed(4)}°`;
    lonDisplay.textContent = `${parseFloat(savedLon).toFixed(4)}°`;
    latInput.value = savedLat;
    lonInput.value = savedLon;
  }

  if (editButton) editButton.addEventListener('click', () => {
    viewMode.classList.add('d-none'); editMode.classList.remove('d-none');
  });

  if (cancelButton) cancelButton.addEventListener('click', () => {
    viewMode.classList.remove('d-none'); editMode.classList.add('d-none');
  });

  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const lat = parseFloat(latInput.value);
      const lon = parseFloat(lonInput.value);
      if (!Number.isFinite(lat) || !Number.isFinite(lon) ||
          lat < -90 || lat > 90 || lon < -180 || lon > 180) {
        alert('Please enter valid lon/lat.'); return;
      }
      latDisplay.textContent = `${lat.toFixed(4)}°`;
      lonDisplay.textContent = `${lon.toFixed(4)}°`;
      localStorage.setItem('groundLat', lat);
      localStorage.setItem('groundLon', lon);
      updateGroundMarkerPosition();
      if (window.__lastRocket) updateGSVector(window.__lastRocket.lat, window.__lastRocket.lon);
      viewMode.classList.remove('d-none');
      editMode.classList.add('d-none');
    });
  }
}

/* ---------- Rocket position + Ground→Rocket vector ---------- */
function toBearingDegrees(rad) { return (rad * 180 / Math.PI + 360) % 360; }
function haversineMeters(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const φ1 = lat1 * Math.PI/180, φ2 = lat2 * Math.PI/180;
  const dφ = (lat2-lat1) * Math.PI/180, dλ = (lon2-lon1) * Math.PI/180;
  const a = Math.sin(dφ/2)**2 + Math.cos(φ1)*Math.cos(φ2)*Math.sin(dλ/2)**2;
  return 2*R*Math.asin(Math.sqrt(a));
}
function initialBearing(lat1, lon1, lat2, lon2) {
  const φ1 = lat1 * Math.PI/180, φ2 = lat2 * Math.PI/180;
  const λ1 = lon1 * Math.PI/180, λ2 = lon2 * Math.PI/180;
  const y = Math.sin(λ2-λ1) * Math.cos(φ2);
  const x = Math.cos(φ1)*Math.sin(φ2) - Math.sin(φ1)*Math.cos(φ2)*Math.cos(λ2-λ1);
  return toBearingDegrees(Math.atan2(y,x));
}
function bearingToCardinal(b) {
  const dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW','N'];
  return dirs[Math.round(b/22.5)];
}
function updateGSVector(rocketLat, rocketLon) {
  const gsDistanceEl = document.getElementById('gsDistance');
  const gsBearingEl  = document.getElementById('gsBearing');
  if (!gsDistanceEl || !gsBearingEl) return;
  const gsLat = parseFloat(localStorage.getItem('groundLat'));
  const gsLon = parseFloat(localStorage.getItem('groundLon'));
  if (!Number.isFinite(gsLat) || !Number.isFinite(gsLon)) {
    gsDistanceEl.textContent = '—';
    gsBearingEl.textContent = '—';
    return;
  }
  const d = haversineMeters(gsLat, gsLon, rocketLat, rocketLon);
  const brg = initialBearing(gsLat, gsLon, rocketLat, rocketLon);
  gsDistanceEl.textContent = d >= 1000 ? `${(d/1000).toFixed(2)} km` : `${d.toFixed(0)} m`;
  gsBearingEl.textContent  = `${brg.toFixed(0)}° (${bearingToCardinal(brg)})`;
}
window.updateRocketPosition = function(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return;
  ensureRocketMarker();
  if (!leafletMap.hasLayer(rocketMarker)) rocketMarker.addTo(leafletMap);
  rocketMarker.setLatLng([lat, lon]);
  window.__lastRocket = { lat, lon };
  updateGSVector(lat, lon);
  const rLat = document.getElementById('rocketLat');
  const rLon = document.getElementById('rocketLon');
  if (rLat) rLat.textContent = `${lat.toFixed(4)}°`;
  if (rLon) rLon.textContent = `${lon.toFixed(4)}°`;
};
