/** Coordinated joint drives in the immutable rest-world assembly frame. */
export function interpolateTracks(assembly, tracks, fraction) {
  if (!Number.isFinite(fraction) || fraction < 0 || fraction > 1 || !Object.keys(tracks).length) return null;
  const values = { ...assembly.valores };
  for (const [id, track] of Object.entries(tracks)) {
    const joint = assembly.articulaciones.find(item => item.id === id && item.tipo !== "fijo");
    if (!joint || !Number.isFinite(track.from) || !Number.isFinite(track.to) ||
        track.from < joint.limites[0] || track.from > joint.limites[1] ||
        track.to < joint.limites[0] || track.to > joint.limites[1]) return null;
    values[id] = track.from + (track.to - track.from) * fraction;
  }
  return values;
}
