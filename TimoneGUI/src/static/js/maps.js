document.addEventListener('DOMContentLoaded', function() {
    // Initialize map when the maps tab is shown
    document.querySelector('#maps-tab').addEventListener('shown.bs.tab', function (e) {
        initLocalMap();
        initGroundGPSControls();
    });
});

function initLocalMap() {
    // Check if map is already initialized
    if (window.imageMap) return;

    // Create map with NSW center coordinates
    const map = L.map('imageMap', {
        crs: L.CRS.Simple,
        minZoom: -2,
        maxZoom: 2
    });

    // Get image dimensions (update these with your image dimensions)
    const w = 3000;  // width of your image
    const h = 2000;  // height of your image
    
    // Calculate bounds based on image dimensions
    const southWest = map.unproject([0, h], 0);
    const northEast = map.unproject([w, 0], 0);
    const bounds = new L.LatLngBounds(southWest, northEast);

    // Add the image overlay
    const image = L.imageOverlay('/static/images/maps/nsw-satellite.jpg', bounds).addTo(map);

    // Set map view to fit the image
    map.fitBounds(bounds);
    map.setMaxBounds(bounds);

    // Store map reference
    window.imageMap = map;
}

// Ground Station GPS functionality
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

    // Load saved coordinates if they exist
    const savedLat = localStorage.getItem('groundLat');
    const savedLon = localStorage.getItem('groundLon');
    if (savedLat && savedLon) {
        latDisplay.textContent = `${parseFloat(savedLat).toFixed(4)}°`;
        lonDisplay.textContent = `${parseFloat(savedLon).toFixed(4)}°`;
        latInput.value = savedLat;
        lonInput.value = savedLon;
    }

    // Toggle edit mode
    editButton.addEventListener('click', () => {
        viewMode.classList.add('d-none');
        editMode.classList.remove('d-none');
    });

    // Cancel editing
    cancelButton.addEventListener('click', () => {
        viewMode.classList.remove('d-none');
        editMode.classList.add('d-none');
    });

    // Handle form submission
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const lat = parseFloat(latInput.value);
        const lon = parseFloat(lonInput.value);

        // Update display
        latDisplay.textContent = `${lat.toFixed(4)}°`;
        lonDisplay.textContent = `${lon.toFixed(4)}°`;

        // Save to localStorage
        localStorage.setItem('groundLat', lat);
        localStorage.setItem('groundLon', lon);

        // Switch back to view mode
        viewMode.classList.remove('d-none');
        editMode.classList.add('d-none');

        // If map exists, update ground station marker
        if (window.imageMap) {
            updateGroundStationMarker(lat, lon);
        }
    });
}

function updateGroundStationMarker(lat, lon) {
    // Remove existing marker if it exists
    if (window.groundStationMarker) {
        window.imageMap.removeLayer(window.groundStationMarker);
    }

    // Add new marker
    window.groundStationMarker = L.marker([lat, lon], {
        icon: L.icon({
            iconUrl: '/static/images/ground-station.png',
            iconSize: [32, 32],
            iconAnchor: [16, 16]
        })
    }).addTo(window.imageMap);
}