document.addEventListener('DOMContentLoaded', function() {
    const radio433Form = document.getElementById('radio433Form');
    const radio915Form = document.getElementById('radio915Form');

    // Inputs for 433
    const bw433 = document.getElementById('bandwidth433');
    const cr433 = document.getElementById('codingRate433');
    const sf433 = document.getElementById('spreadingFactor433');

    // Inputs for 915
    const bw915 = document.getElementById('bandwidth915');
    const cr915 = document.getElementById('codingRate915');
    const sf915 = document.getElementById('spreadingFactor915');

    // ---- Load settings on page load ----
    loadSettings();

    async function loadSettings() {
        // Try server first
        try {
            const res = await fetch('/api/radio/settings', { method: 'GET' });
            if (!res.ok) throw new Error('Failed to load settings');
            const data = await res.json();
            applySettingsToForms(data);
            localStorage.setItem('radio_settings_cache', JSON.stringify(data));
        } catch (err) {
            console.error('Error loading settings:', err);
            // Fallback to any cached local copy
            const cache = localStorage.getItem('radio_settings_cache');
            if (cache) {
                try {
                    const data = JSON.parse(cache);
                    applySettingsToForms(data);
                } catch {}
            }
        }
    }

    function applySettingsToForms(data) {
        if (data && data['433']) {
            if (bw433) bw433.value = data['433'].bandwidth;
            if (cr433) cr433.value = data['433'].codingRate;
            if (sf433) sf433.value = data['433'].spreadingFactor;
        }
        if (data && data['915']) {
            if (bw915) bw915.value = data['915'].bandwidth;
            if (cr915) cr915.value = data['915'].codingRate;
            if (sf915) sf915.value = data['915'].spreadingFactor;
        }
    }

    // ---- Submit handler (retains existing functionality) ----
    function handleFormSubmit(event, frequency) {
        event.preventDefault();

        const formData = new FormData(event.target);
        const settings = {
            bandwidth: parseFloat(formData.get('bandwidth')),
            codingRate: formData.get('codingRate'),
            spreadingFactor: parseInt(formData.get('spreadingFactor')),
            frequency: frequency
        };

        // Optional: disable button during save
        const submitBtn = event.submitter || event.target.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;

        fetch('/api/radio/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        })
        .then(async (response) => {
            const json = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(json.error || 'Save failed');

            // Update local cache with server-confirmed state
            if (json.settings) {
                localStorage.setItem('radio_settings_cache', JSON.stringify(json.settings));
            }
            alert(`${frequency} MHz radio settings updated successfully`);
        })
        .catch(error => {
            console.error('Error:', error);
            alert(`Error updating ${frequency} MHz radio settings: ${error.message}`);
        })
        .finally(() => {
            if (submitBtn) submitBtn.disabled = false;
        });
    }

    if (radio433Form) {
        radio433Form.addEventListener('submit', (e) => handleFormSubmit(e, 433));
    }

    if (radio915Form) {
        radio915Form.addEventListener('submit', (e) => handleFormSubmit(e, 915));
    }
});
