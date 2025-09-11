document.addEventListener('DOMContentLoaded', function() {
    const radio433Form = document.getElementById('radio433Form');
    const radio915Form = document.getElementById('radio915Form');

    function handleFormSubmit(event, frequency) {
        event.preventDefault();
        const formData = new FormData(event.target);
        const settings = {
            bandwidth: parseFloat(formData.get('bandwidth')),
            codingRate: formData.get('codingRate'),
            spreadingFactor: parseInt(formData.get('spreadingFactor')),
            frequency: frequency
        };

        // Send settings to backend
        fetch('/api/radio/settings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(settings)
        })
        .then(response => response.json())
        .then(data => {
            alert(`${frequency} MHz radio settings updated successfully`);
        })
        .catch(error => {
            console.error('Error:', error);
            alert(`Error updating ${frequency} MHz radio settings`);
        });
    }

    if (radio433Form) {
        radio433Form.addEventListener('submit', (e) => handleFormSubmit(e, 433));
    }

    if (radio915Form) {
        radio915Form.addEventListener('submit', (e) => handleFormSubmit(e, 915));
    }
});