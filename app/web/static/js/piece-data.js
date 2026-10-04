// One response carries both the STL and its exact component triangle ranges.
export function decodePieceBundle(buffer) {
  if (!(buffer instanceof ArrayBuffer)) throw new Error("Malla inválida");
  if (buffer.byteLength < 4 || new TextDecoder().decode(new Uint8Array(buffer, 0, 4)) !== "FJP1") {
    return { stl: buffer, manifest: null };
  }
  if (buffer.byteLength < 8) throw new Error("Paquete de piezas incompleto");
  const headerLength = new DataView(buffer).getUint32(4, true);
  const start = 8 + headerLength;
  if (start + 84 > buffer.byteLength) throw new Error("Paquete de piezas incompleto");
  const manifest = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 8, headerLength)));
  const stl = buffer.slice(start);
  const triangleCount = new DataView(stl).getUint32(80, true);
  if (manifest.version !== 1 || manifest.triangulos !== triangleCount ||
      stl.byteLength !== 84 + triangleCount * 50 || !Array.isArray(manifest.piezas)) {
    throw new Error("La geometría y el árbol de piezas no coinciden");
  }
  const coverage = new Uint8Array(triangleCount);
  const names = new Set();
  for (const piece of manifest.piezas) {
    if (typeof piece.nombre !== "string" || !piece.nombre || names.has(piece.nombre) ||
        !Array.isArray(piece.bbox) || piece.bbox.length !== 6 || !piece.bbox.every(Number.isFinite) ||
        piece.bbox[0] > piece.bbox[1] || piece.bbox[2] > piece.bbox[3] || piece.bbox[4] > piece.bbox[5] ||
        !Array.isArray(piece.rangos)) throw new Error("Datos de pieza inválidos");
    names.add(piece.nombre);
    for (const range of piece.rangos) {
      if (!Array.isArray(range) || range.length !== 2) throw new Error("Rango de pieza inválido");
      const [first, count] = range;
      if (!Number.isInteger(first) || !Number.isInteger(count) || first < 0 || count < 1 || first + count > triangleCount) {
        throw new Error("Rango de pieza fuera de la malla");
      }
      for (let i = first; i < first + count; i++) {
        if (coverage[i]) throw new Error("Triángulo asignado a varias piezas");
        coverage[i] = 1;
      }
    }
  }
  if (coverage.some(value => value !== 1)) throw new Error("Hay geometría sin pieza asignada");
  return { stl, manifest };
}
