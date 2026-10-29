// Keep camera frames upright when the phone is turned further than its screen
// (e.g. screen rotation locked, or the stream keeping its start orientation).
// Angles are degrees of counter-clockwise device rotation from its natural orientation,
// the same convention as screen.orientation.angle (landscape-primary phone = 90).

export const normalize = angle => ((Math.round(angle) % 360) + 360) % 360;

const distance = (a, b) => Math.abs(((a - b + 540) % 360) - 180);

const FLAT = 4;        // m/s²: below this in the screen plane the phone lies flat → orientation unknown
const HYSTERESIS = 60; // stay on the current 90° step until the phone is turned more than this

// Raw device angle from devicemotion accelerationIncludingGravity (x → device right, y → device top).
export function gravityAngle(x, y, sign = 1) {
  if (x == null || y == null || Math.hypot(x, y) < FLAT) return null;
  return normalize(Math.atan2(sign * x, sign * y) * 180 / Math.PI);
}

// Some browsers (notably iOS Safari) report gravity with the opposite sign. At the first reading
// the phone is assumed to be held roughly as its screen shows; a reading ~180° away means inverted.
export function gravitySign(x, y, screenAngle) {
  const angle = gravityAngle(x, y);
  if (angle === null) return null;
  return distance(angle, screenAngle) > 135 ? -1 : 1;
}

// Snap to 0/90/180/270 with hysteresis around the current step.
export function snapAngle(angle, previous = null) {
  if (angle === null) return previous;
  if (previous !== null && distance(angle, previous) <= HYSTERESIS) return previous;
  return normalize(Math.round(angle / 90) * 90);
}

// How far the frame content is turned against the world: the device turned beyond the screen.
export const frameRotation = (deviceAngle, screenAngle) =>
  deviceAngle === null ? 0 : normalize(deviceAngle - screenAngle);

// Low-pass the gravity vector; averaging the vector (not the angle) avoids the 359°→0° wrap.
export function smoothGravity(previous, x, y, alpha = 0.2) {
  if (x == null || y == null) return previous;
  if (!previous) return {x, y};
  return {x: previous.x + alpha * (x - previous.x), y: previous.y + alpha * (y - previous.y)};
}

// Diagonal tilt left over after the 90° step, in [-180, 180): positive = turned further counter-clockwise.
export const tiltFrom = (angle, quarter) =>
  angle === null || quarter === null ? 0 : ((angle - quarter + 540) % 360) - 180;

const TILT_ON = 10;  // start correcting the tilt beyond this many degrees …
const TILT_OFF = 6;  // … and keep correcting until it drops below this (no flicker at the edge)

export function tiltDeadzone(tilt, active) {
  const on = Math.abs(tilt) >= (active ? TILT_OFF : TILT_ON);
  return {tilt: on ? tilt : 0, active: on};
}

// Zoom that makes a width×height frame turned by `degrees` fill the same width×height box again:
// the output is the largest same-aspect rectangle inside the turned frame (corners are cropped).
export function cropZoom(width, height, degrees) {
  const r = degrees * Math.PI / 180, c = Math.abs(Math.cos(r)), s = Math.abs(Math.sin(r));
  return Math.max((width * c + height * s) / width, (width * s + height * c) / height);
}

// Canvas size, rotation and zoom to draw a width×height video frame turned back by
// `rotation` (90° steps, changes the canvas shape) plus `tilt` (diagonal, cropped, same shape).
// Canvas angles are clockwise, so undoing a counter-clockwise device turn is a negative angle.
export function uprightLayout(width, height, rotation, tilt = 0) {
  const sideways = rotation % 180 !== 0;
  const canvasWidth = sideways ? height : width;
  const canvasHeight = sideways ? width : height;
  return {
    canvasWidth,
    canvasHeight,
    radians: -(rotation + tilt) * Math.PI / 180,
    zoom: tilt ? cropZoom(canvasWidth, canvasHeight, tilt) : 1,
  };
}
