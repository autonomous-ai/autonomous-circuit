// threeFit — the camera arithmetic for the 3D board view, kept away from
// three.js so it can be tested in node without a GPU.
//
// Everything here works on plain arrays: a box is {min:[x,y,z], max:[x,y,z]},
// an offset is [x,y,z]. Board3DView owns the WebGL side; this owns the maths
// it must not get wrong, because a camera that fits the board wrong reads as
// "the 3D tab is broken" even when the renderer is fine.

/** Centre and bounding-sphere radius of a box. Radius of a degenerate box is
 * 0 — callers guard, so an empty GLB fits at a sane default instead of NaN. */
export function boxCenterAndRadius(min, max) {
  const center = [
    (min[0] + max[0]) / 2,
    (min[1] + max[1]) / 2,
    (min[2] + max[2]) / 2,
  ];
  const dx = max[0] - min[0];
  const dy = max[1] - min[1];
  const dz = max[2] - min[2];
  const radius = Math.sqrt(dx * dx + dy * dy + dz * dz) / 2;
  return { center, radius };
}

/**
 * Distance from which a perspective camera sees the whole bounding sphere.
 *
 * The frustum's limiting half-angle is the smaller of vertical and horizontal
 * (portrait panes clip left/right, landscape panes clip top/bottom), and the
 * sphere is tangent to the frustum at distance r / sin(halfAngle) — sin, not
 * tan: tan fits a flat disc and clips the sphere's near bulge.
 *
 * `margin` > 1 keeps the board off the very edge of the pane.
 */
export function fitDistance(radius, fovYDeg, aspect, margin = 1.15) {
  const safeRadius = radius > 0 ? radius : 1;
  const fovY = (Math.max(1, Math.min(179, fovYDeg)) * Math.PI) / 180;
  const safeAspect = aspect > 0 ? aspect : 1;
  const fovX = 2 * Math.atan(Math.tan(fovY / 2) * safeAspect);
  const half = Math.min(fovY, fovX) / 2;
  return (safeRadius * margin) / Math.sin(half);
}

/** The axis a PCB is thin along — index of the smallest box extent. Flip and
 * rotate are defined around this axis so they mean "the other side of the
 * board" and "spin the board", whatever orientation the exporter chose. */
export function thinnestAxis(min, max) {
  const ext = [max[0] - min[0], max[1] - min[1], max[2] - min[2]];
  let axis = 0;
  if (ext[1] < ext[axis]) axis = 1;
  if (ext[2] < ext[axis]) axis = 2;
  return axis;
}

/** Ctrl+F — view the board from the other side: reflect the camera offset
 * through the board plane (negate the thin-axis component). */
export function flipOffset(offset, axis) {
  const out = offset.slice();
  out[axis] = -out[axis];
  return out;
}

/** 9 — rotate the view 90° about the board normal, keeping height: rotate the
 * two in-plane components, leave the thin-axis component alone. */
export function rotateOffset90(offset, axis) {
  const [a, b] = [0, 1, 2].filter((i) => i !== axis);
  const out = offset.slice();
  //  (a, b) -> (-b, a): a proper 90° turn regardless of which axis is normal.
  const va = offset[a];
  const vb = offset[b];
  out[a] = -vb;
  out[b] = va;
  return out;
}
