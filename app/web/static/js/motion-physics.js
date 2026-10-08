/** Compression spring, with all physical inputs supplied by the user.
 * Positions are mm; stiffness N/mm; reference force N; output N.
 * direction is +1 when increasing joint position compresses the spring.
 */
export function springForce({ stiffness, referenceForce, referencePosition, position, direction }) {
  if (![stiffness, referenceForce, referencePosition, position].every(Number.isFinite) ||
      stiffness < 0 || referenceForce < 0 || ![1, -1].includes(direction)) return null;
  return Math.max(0, referenceForce + stiffness * direction * (position - referencePosition));
}
