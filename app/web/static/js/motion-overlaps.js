/** Conservative world AABB of a local Box3 under a rigid 4x4 matrix.
 * The eight vertices need not be allocated: abs(R) times half-extents
 * gives the same bounds. This is broad-phase geometry, not contact. */
export function transformedBounds(bounds, matrix, id) {
  const min = bounds.min, max = bounds.max;
  const cx = (min.x + max.x) / 2, cy = (min.y + max.y) / 2, cz = (min.z + max.z) / 2;
  const ex = (max.x - min.x) / 2, ey = (max.y - min.y) / 2, ez = (max.z - min.z) / 2;
  const center = [
    matrix[0] * cx + matrix[4] * cy + matrix[8] * cz + matrix[12],
    matrix[1] * cx + matrix[5] * cy + matrix[9] * cz + matrix[13],
    matrix[2] * cx + matrix[6] * cy + matrix[10] * cz + matrix[14],
  ];
  const extent = [
    Math.abs(matrix[0]) * ex + Math.abs(matrix[4]) * ey + Math.abs(matrix[8]) * ez,
    Math.abs(matrix[1]) * ex + Math.abs(matrix[5]) * ey + Math.abs(matrix[9]) * ez,
    Math.abs(matrix[2]) * ex + Math.abs(matrix[6]) * ey + Math.abs(matrix[10]) * ez,
  ];
  return { id, min: center.map((v, i) => v - extent[i]), max: center.map((v, i) => v + extent[i]) };
}

/** Sweep on X and reject active candidates on Y/Z. Inclusive overlap is
 * deliberate: touching boxes remain possible contacts. `candidates` helps
 * quantify broad-phase work in tests and diagnostics. */
export function overlapPairs(boxes) {
  const ordered = [...boxes].sort((a, b) => a.min[0] - b.min[0]);
  let active = [];
  const pairs = new Set();
  let candidates = 0;
  for (const box of ordered) {
    active = active.filter(other => other.max[0] >= box.min[0]);
    for (const other of active) {
      candidates++;
      if (other.max[1] < box.min[1] || box.max[1] < other.min[1] ||
          other.max[2] < box.min[2] || box.max[2] < other.min[2]) continue;
      pairs.add(`${Math.min(box.id, other.id)}:${Math.max(box.id, other.id)}`);
    }
    active.push(box);
  }
  return { pairs, candidates };
}
