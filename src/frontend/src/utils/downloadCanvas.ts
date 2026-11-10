/** Save a canvas' picture as a PNG file, through the browser's download. */
export function downloadCanvas(canvas: HTMLCanvasElement, fileName: string): void {
    canvas.toBlob((blob) => {
        if (!blob) return;
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = fileName;
        link.click();
    });
}
