import html2canvas from 'html2canvas';

export async function takeScreenshot() {
  const canvas = await html2canvas(document.body);
  canvas.toBlob(blob => {
    if (blob) {
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'screenshot.png';
      link.click();
    }
  });
}
