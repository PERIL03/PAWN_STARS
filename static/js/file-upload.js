document.addEventListener('DOMContentLoaded', function() {
    const form = document.querySelector('form');
    const fileInputs = document.querySelectorAll('input[type="file"]');
    const submitButton = document.querySelector('.btn');

    let selectedFile = null;
    let actionType = null;
    let fileType = null;

    // File selection event listeners
    fileInputs.forEach(input => {
        input.addEventListener('change', function() {
            selectedFile = this.files[0];
            
            // Determine action type and file type based on input
            if (input.closest('.Encode')) {
                actionType = 'encode';
            } else if (input.closest('.Decode')) {
                actionType = 'decode';
            }

            fileType = input.accept.includes('text') ? 'text' : 'image';
        });
    });

    // Submit button click handler
    submitButton.addEventListener('click', async function(e) {
        e.preventDefault();

        if (!selectedFile || !actionType || !fileType) {
            alert('Please select a file and choose encode/decode option');
            return;
        }

        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('file_type', fileType);

        try {
            const url = actionType === 'encode' ? '/encode' : '/decode';
            const response = await fetch(url, {
                method: 'POST',
                body: formData
            });

            if (response.ok) {
                const blob = await response.blob();
                const downloadUrl = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = downloadUrl;

                const outputFileName = actionType === 'encode' ? 'encoded_output.pgn'
                                    : fileType === 'text' ? 'decoded_output.txt'
                                    : 'decoded_output.png';
                a.download = outputFileName;
                document.body.appendChild(a);
                a.click();
                a.remove();
            } else {
                const errorData = await response.json();
                alert(`Error: ${errorData.error}`);
            }
        } catch (error) {
            console.error('Error:', error);
            alert('An error occurred while processing your file.');
        }
    });
});