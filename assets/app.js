const socket = io(`http://${window.location.host}`); // Initialize socket.io connection
let errorContainer = document.getElementById('error-container');

// Start the application
// If any code is needed to draw overlays on the video canvas, keep that logic here. Otherwise, remove code that updates or displays anything apart from the video/canvas.

document.addEventListener('DOMContentLoaded', () => {
    initSocketIO();
    // Only initialize what is essential for video/canvas overlays
});

function initSocketIO() {
    socket.on('connect', () => {
        if (errorContainer) {
            errorContainer.style.display = 'none';
            errorContainer.textContent = '';
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
