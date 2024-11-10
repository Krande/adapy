import React from "react";

export function handleFileUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file && file.name.endsWith('.desiredSuffix')) {
        // You can process the file further or display it
        console.log('Uploaded file:', file.name);
    } else {
        console.log('Invalid file type. Please upload a file with the correct suffix.');
    }
};