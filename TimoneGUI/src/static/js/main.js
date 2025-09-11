// Add Chart.js to base.html before this file
document.addEventListener('DOMContentLoaded', function() {
    // Global charts registry
    const charts = {};
    
    // Initialize continuity charts
    initContinuityCharts();
    
    // Initialize Add Graph functionality
    initAddGraphButton();
    
    function initContinuityCharts() {
        const drogueContinuityCtx = document.getElementById('drogueContinuityChart');
        const mainContinuityCtx = document.getElementById('mainContinuityChart');

        // Debug logging
        console.log('Initializing continuity charts');
        console.log('Drogue context:', drogueContinuityCtx);
        console.log('Main context:', mainContinuityCtx);

        if (drogueContinuityCtx && mainContinuityCtx) {
            // Drogue Chart
            const drogueChart = new Chart(drogueContinuityCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Drogue',
                        data: [],
                        stepped: true,
                        borderColor: 'rgb(75, 192, 192)',
                        tension: 0.1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            min: 0,
                            max: 1,
                            ticks: {
                                stepSize: 1,
                                callback: value => value === 0 ? 'Disconnected' : 'Connected'
                            }
                        }
                    },
                    plugins: {
                        legend: {
                            display: false
                        }
                    }
                }
            });

            // Main Chart (separate instance)
            const mainChart = new Chart(mainContinuityCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Main',
                        data: [],
                        stepped: true,
                        borderColor: 'rgb(255, 99, 132)',
                        tension: 0.1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            min: 0,
                            max: 1,
                            ticks: {
                                stepSize: 1,
                                callback: value => value === 0 ? 'Disconnected' : 'Connected'
                            }
                        }
                    },
                    plugins: {
                        legend: {
                            display: false
                        }
                    }
                }
            });

            // Store charts in global registry
            charts['drogueContinuityChart'] = drogueChart;
            charts['mainContinuityChart'] = mainChart;

            // Initialize resize observers for both charts
            initResize(drogueContinuityCtx.parentElement);
            initResize(mainContinuityCtx.parentElement);
        }
    }

    function initAddGraphButton() {
        const addGraphBtn = document.getElementById('addGraphBtn');
        if (addGraphBtn) {
            addGraphBtn.addEventListener('click', handleAddGraph);
        }
    }

    function handleAddGraph() {
        const select = document.getElementById('telemetrySelect');
        const selectedOptions = Array.from(select.selectedOptions).map(option => ({
            value: option.value,
            label: option.text
        }));
        
        if (selectedOptions.length > 0) {
            addNewGraph(selectedOptions);
            const modal = bootstrap.Modal.getInstance(document.getElementById('addGraphModal'));
            if (modal) {
                modal.hide();
            }
            select.selectedIndex = -1; // Reset selection
        }
    }

    function addNewGraph(datasets) {
        const graphContainer = document.getElementById('graphContainer');
        const graphId = 'graph-' + Date.now();
        
        const graphCol = document.createElement('div');
        graphCol.className = 'graph-wrapper';
        graphCol.innerHTML = `
            <div class="card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <h6 class="card-title mb-0">Telemetry Graph</h6>
                    <button class="btn btn-sm btn-danger remove-graph">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
                <div class="card-body">
                    <canvas id="${graphId}"></canvas>
                </div>
            </div>
        `;
        
        graphContainer.appendChild(graphCol);
        
        // Initialize new chart
        const ctx = document.getElementById(graphId);
        const chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: datasets.map(ds => ({
                    label: ds.label,
                    data: [],
                    borderColor: getRandomColor(),
                    tension: 0.1
                }))
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });

        // Add remove event listener
        graphCol.querySelector('.remove-graph').addEventListener('click', () => {
            chart.destroy();
            graphCol.remove();
        });
    }

    function initDragAndDrop(element) {
        element.addEventListener('dragstart', handleDragStart);
        element.addEventListener('dragover', handleDragOver);
        element.addEventListener('drop', handleDrop);
    }

    function handleDragStart(e) {
        e.dataTransfer.setData('text/plain', e.target.id);
        e.target.classList.add('dragging');
    }

    function handleDragOver(e) {
        e.preventDefault();
    }

    function handleDrop(e) {
        e.preventDefault();
        const id = e.dataTransfer.getData('text/plain');
        const draggedElement = document.getElementById(id);
        const dropZone = e.target.closest('.graph-wrapper');
        
        if (dropZone && draggedElement) {
            const rect = dropZone.getBoundingClientRect();
            const draggedRect = draggedElement.getBoundingClientRect();
            
            if (e.clientY < rect.top + rect.height / 2) {
                dropZone.parentNode.insertBefore(draggedElement, dropZone);
            } else {
                dropZone.parentNode.insertBefore(draggedElement, dropZone.nextSibling);
            }
        }
    }

    // Initialize flight data chart
    const ctx = document.getElementById('flightChart');
    if (ctx) {
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: [], // Time points
                datasets: [{
                    label: 'Altitude (m)',
                    data: [],
                    borderColor: 'rgb(75, 192, 192)',
                    tension: 0.1
                }, {
                    label: 'Vertical Velocity (m/s)',
                    data: [],
                    borderColor: 'rgb(255, 99, 132)',
                    tension: 0.1
                }]
            },
            options: {
                responsive: true,
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    }

    // Initialize telemetry chart
    const telemetryCtx = document.getElementById('telemetryChart');
    if (telemetryCtx) {
        new Chart(telemetryCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: []
            },
            options: {
                responsive: true,
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    }

    function getRandomColor() {
        const letters = '0123456789ABCDEF';
        let color = '#';
        for (let i = 0; i < 6; i++) {
            color += letters[Math.floor(Math.random() * 16)];
        }
        return color;
    }

    function updateRadioStatus(radio, connected, healthy) {
        const connectedEl = document.getElementById(`${radio}Connected`);
        const healthEl = document.getElementById(`${radio}Health`);
        
        if (connectedEl) {
            connectedEl.className = `status-indicator ${connected ? 'connected' : 'disconnected'}`;
        }
        
        if (healthEl) {
            healthEl.className = `status-indicator ${healthy ? 'healthy' : 'unhealthy'}`;
        }
    }

    // Example usage:
    // updateRadioStatus('radio433', true, false); // Connected but unhealthy
    // updateRadioStatus('radio915', true, true);  // Connected and healthy

    function initResize(graphElement) {
        const resizeObserver = new ResizeObserver(entries => {
            for (const entry of entries) {
                const graphId = entry.target.querySelector('canvas').id;
                if (charts[graphId]) {
                    charts[graphId].resize();
                }
            }
        });
        
        resizeObserver.observe(graphElement);
    }
});

document.addEventListener('shown.bs.tab', function (event) {
    console.log('Tab shown:', event.target.id);
});