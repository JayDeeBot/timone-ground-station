document.addEventListener('DOMContentLoaded', function() {
    const mapsTab = document.querySelector('#maps-tab');
    if (mapsTab) {
        mapsTab.addEventListener('shown.bs.tab', function () {
            ensureMap();
            initGroundGPSControls();
            refreshMapList(); // load saved maps into dropdown
        });
    }
});

let leafletMap = null;
let currentOverlay = null;
let groundMarker = null; // pulsing dot

function ensureMap() {
    if (leafletMap) return;

    // Offline-only Leaflet map (no base tiles) with gentler wheel zoom
    leafletMap = L.map('imageMap', {
        center: [-33.86, 151.21],
        zoom: 8,
        attributionControl: false,
        preferCanvas: true,
        worldCopyJump: false,
        // ↓ make wheel zoom less sensitive
        wheelPxPerZoomLevel: 180,   // default ~60; higher = less sensitive
        wheelDebounceTime: 80,      // smooths rapid wheel events
        zoomDelta: 0.25,            // smaller zoom steps
        zoomSnap: 0.25,             // allow quarter zoom levels
        scrollWheelZoom: true
    });

    injectPulseStyles();
    initMapManagerUI();
}

// ---- pulsing marker helpers ----
function injectPulseStyles() {
    if (document.getElementById('pulse-style')) return;
    const css = `
    .pulse-wrapper { position: relative; }
    .pulse-dot {
        width: 12px; height: 12px; border-radius: 50%;
        background: #0d6efd;
        box-shadow: 0 0 0 rgba(13,110,253,0.7);
        animation: pulse 2s infinite;
    }
    @keyframes pulse {
        0%   { box-shadow: 0 0 0 0 rgba(13,110,253,0.7); }
        70%  { box-shadow: 0 0 0 14px rgba(13,110,253,0); }
        100% { box-shadow: 0 0 0 0 rgba(13,110,253,0); }
    }`;
    const style = document.createElement('style');
    style.id = 'pulse-style';
    style.textContent = css;
    document.head.appendChild(style);
}

function getSavedGroundLatLon() {
    const lat = parseFloat(localStorage.getItem('groundLat'));
    const lon = parseFloat(localStorage.getItem('groundLon'));
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    return { lat, lon };
}

function ensureGroundMarker() {
    if (groundMarker || !leafletMap) return;

    const icon = L.divIcon({
        className: 'pulse-wrapper',
        html: '<div class="pulse-dot"></div>',
        iconSize: [12, 12],
        iconAnchor: [6, 6]
    });

    // Create marker at (0,0) initially; will be moved into place
    groundMarker = L.marker([0, 0], { icon, zIndexOffset: 1000, interactive: false });
    groundMarker.addTo(leafletMap);
}

function updateGroundMarkerPosition() {
    if (!leafletMap) return;
    const pos = getSavedGroundLatLon();
    if (!pos) return;
    ensureGroundMarker();
    groundMarker.setLatLng([pos.lat, pos.lon]);
}

// ---- corners helpers (lon,lat arrays) ----
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
        }
        return true;
    } catch {
        return false;
    }
}

function cornersToBounds(c) {
    const lons = [c.top_left[0], c.top_right[0], c.bottom_right[0], c.bottom_left[0]].map(Number);
    const lats = [c.top_left[1], c.top_right[1], c.bottom_right[1], c.bottom_left[1]].map(Number);
    if (lons.some(x => !Number.isFinite(x)) || lats.some(x => !Number.isFinite(x))) return null;
    const west = Math.min(...lons);
    const east = Math.max(...lons);
    const south = Math.min(...lats);
    const north = Math.max(...lats);
    if (!(north > south && east > west)) return null; // avoid zero/negative spans
    return { south, west, north, east };
}

function initMapManagerUI() {
    const mapSelect = document.getElementById('mapSelect');
    const uploadForm = document.getElementById('mapUploadForm');

    if (mapSelect) {
        mapSelect.addEventListener('change', () => {
            const id = mapSelect.value;
            if (!id) {
                clearOverlayAndBounds();
                return;
            }
            const metaList = window._mapsIndex?.maps || [];
            const meta = metaList.find(m => m.id === id);
            if (meta) {
                try {
                    renderSatellite(meta);
                } catch (e) {
                    console.error('Render failed:', e);
                    clearOverlayAndBounds();
                }
            }
        });
    }

    if (uploadForm) {
        uploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const fd = new FormData();
            const fileEl = document.getElementById('mapFile');
            const nameEl = document.getElementById('mapName');
            if (!fileEl?.files?.[0]) {
                alert('Please choose an image file to upload.');
                return;
            }
            fd.append('file', fileEl.files[0]);
            if (nameEl?.value?.trim()) fd.append('name', nameEl.value.trim());

            // Require corners (lon,lat)
            const tlEl = document.getElementById('tlInput') || document.getElementById('topLeftInput');
            const trEl = document.getElementById('trInput') || document.getElementById('topRightInput');
            const brEl = document.getElementById('brInput') || document.getElementById('bottomRightInput');
            const blEl = document.getElementById('blInput') || document.getElementById('bottomLeftInput');

            const tl = tlEl?.value?.trim();
            const tr = trEl?.value?.trim();
            const br = brEl?.value?.trim();
            const bl = blEl?.value?.trim();

            const asPair = (s) => {
                const parts = (s || "").split(',').map(p => p.trim());
                if (parts.length !== 2) return null;
                const lon = Number(parts[0]);
                const lat = Number(parts[1]);
                if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
                if (lon < -180 || lon > 180 || lat < -90 || lat > 90) return null;
                return `${lon},${lat}`; // normalized
            };

            if (!(tl && tr && br && bl)) {
                alert('Please provide all four corners as "lon,lat".');
                return;
            }

            const tlN = asPair(tl), trN = asPair(tr), brN = asPair(br), blN = asPair(bl);
            if ([tlN, trN, brN, blN].some(v => v === null)) {
                alert('Corner format must be "lon,lat" (e.g., 143.1972,-30.6716).');
                return;
            }

            fd.append('top_left', tlN);
            fd.append('top_right', trN);
            fd.append('bottom_right', brN);
            fd.append('bottom_left', blN);

            try {
                const res = await fetch('/api/maps', { method: 'POST', body: fd });
                const json = await res.json().catch(async () => ({ error: await res.text() }));
                if (!res.ok) throw new Error(json.error || 'Upload failed');

                await refreshMapList(json.map.id); // select new map
                alert('Map uploaded and saved.');
                uploadForm.reset();
            } catch (err) {
                console.error('Upload error:', err);
                alert('Error: ' + err.message);
            }
        });
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
        console.debug('Maps (valid corners):', valid);

        const mapSelect = document.getElementById('mapSelect');
        if (!mapSelect) return;

        const priorValue = selectId || mapSelect.value;
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

function renderSatellite(meta) {
    if (!leafletMap || !meta || !isValidCorners(meta.corners)) {
        console.warn('Skipping render; invalid corners:', meta);
        clearOverlayAndBounds();
        return;
    }

    const b = cornersToBounds(meta.corners);
    if (!b) {
        console.warn('Skipping render; degenerate computed bounds from corners:', meta);
        clearOverlayAndBounds();
        return;
    }

    const { south, west, north, east } = b;

    // Replace existing overlay
    if (currentOverlay) {
        leafletMap.removeLayer(currentOverlay);
        currentOverlay = null;
    }

    try {
        const bounds = L.latLngBounds(
            L.latLng(south, west), // SW
            L.latLng(north, east)  // NE
        );

        currentOverlay = L.imageOverlay(meta.url, bounds, { opacity: 1.0 }).addTo(leafletMap);
        leafletMap.fitBounds(bounds, { padding: [20, 20] });
        const padded = bounds.pad(0.05);
        leafletMap.setMaxBounds(padded);

        // (NEW) render/update ground station dot on top
        updateGroundMarkerPosition();
    } catch (e) {
        console.error('Leaflet failed to apply bounds:', e);
        clearOverlayAndBounds();
    }
}

/* ---------- Ground Station GPS (unchanged + marker hook) ---------- */
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

    if (editButton) {
        editButton.addEventListener('click', () => {
            viewMode.classList.add('d-none');
            editMode.classList.remove('d-none');
        });
    }

    if (cancelButton) {
        cancelButton.addEventListener('click', () => {
            viewMode.classList.remove('d-none');
            editMode.classList.add('d-none');
        });
    }

    if (form) {
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            const lat = parseFloat(latInput.value);
            const lon = parseFloat(lonInput.value);

            latDisplay.textContent = `${lat.toFixed(4)}°`;
            lonDisplay.textContent = `${lon.toFixed(4)}°`;

            localStorage.setItem('groundLat', lat);
            localStorage.setItem('groundLon', lon);

            // (NEW) move/update the pulsing dot immediately
            updateGroundMarkerPosition();

            viewMode.classList.remove('d-none');
            editMode.classList.add('d-none');
        });
    }
}
