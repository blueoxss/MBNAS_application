/**
 * Save a Blob the console already holds (fetched with the Authorization header, never a link that carries a token) as a
 * file. Returns false when the browser context cannot create object URLs (e.g. the unit-test DOM): the caller still shows
 * the hashes of the bytes it received.
 */
export function saveBlob(blob: Blob, fileName: string): boolean {
  if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function' || typeof document === 'undefined') return false;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return true;
}
