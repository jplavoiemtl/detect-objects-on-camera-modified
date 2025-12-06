const socket = io(`http://${window.location.host}`); // Initialize socket.io connection
const errorContainer = document.getElementById('error-container');
const confidenceSlider = document.getElementById('confidenceSlider');
const confidenceValue = document.getElementById('confidenceValue');
const DEFAULT_CONFIDENCE = 0.6;

document.addEventListener('DOMContentLoaded', () => {
    initControls();
    initSocketIO();
});

function initControls() {
    // Set initial displayed value
    updateConfidenceDisplay(confidenceSlider?.value ?? DEFAULT_CONFIDENCE);

    if (confidenceSlider) {
        confidenceSlider.addEventListener('change', () => {
            const value = parseFloat(confidenceSlider.value);
            if (Number.isFinite(value)) {
                updateConfidenceDisplay(value);
                socket.emit('override_th', value);
            }
        });
    }
}

function updateConfidenceDisplay(value) {
    if (confidenceValue) {
        confidenceValue.textContent = Number(value).toFixed(2);
    }
}

function initSocketIO() {
    socket.on('connect', () => {
        if (errorContainer) {
            errorContainer.style.display = 'none';
            errorContainer.textContent = '';
        }

        // Re-send current slider value on reconnect to keep backend in sync
        const value = confidenceSlider ? parseFloat(confidenceSlider.value) : DEFAULT_CONFIDENCE;
        if (Number.isFinite(value)) {
            socket.emit('override_th', value);
        }
    });

    socket.on('disconnect', () => {
        if (errorContainer) {
            errorContainer.textContent = 'Connection to the board lost. Please check the connection.';
            errorContainer.style.display = 'block';
        }
    });

    // Preserve only overlay/canvas-related socket logic
    // socket.on('detection', ...) — Only keep if it calls overlay/canvas code
}
