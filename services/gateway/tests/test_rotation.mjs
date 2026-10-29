// node --test services/gateway/tests/test_rotation.mjs
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  cropZoom, frameRotation, gravityAngle, gravitySign, normalize, smoothGravity, snapAngle, tiltDeadzone, tiltFrom,
  uprightLayout,
} from '../static/rotation.js';

const G = 9.8;

test('gravity angle follows screen.orientation.angle convention', () => {
  assert.equal(gravityAngle(0, G), 0);      // upright portrait
  assert.equal(gravityAngle(G, 0), 90);     // turned counter-clockwise (top to the left)
  assert.equal(gravityAngle(0, -G), 180);   // upside down
  assert.equal(gravityAngle(-G, 0), 270);   // turned clockwise
  assert.equal(gravityAngle(0, -G, -1), 0); // inverted-sign browser
});

test('phone lying flat gives no angle', () => {
  assert.equal(gravityAngle(0.5, 1), null);
  assert.equal(gravitySign(0.5, 1, 0), null);
});

test('gravity sign detects inverted reporting from the first reading', () => {
  assert.equal(gravitySign(0, G, 0), 1);
  assert.equal(gravitySign(0, -G, 0), -1);   // upright phone reported as upside down
  assert.equal(gravitySign(G, 0, 90), 1);
  assert.equal(gravitySign(-G, 0, 90), -1);
  assert.equal(gravitySign(G, 0, 0), 1);     // locked portrait screen, phone held landscape: not inverted
});

test('snap uses 90° steps with hysteresis', () => {
  assert.equal(snapAngle(10), 0);
  assert.equal(snapAngle(80), 90);
  assert.equal(snapAngle(350), 0);
  assert.equal(snapAngle(50, 0), 0);         // within 60° of current step: stay
  assert.equal(snapAngle(70, 0), 90);
  assert.equal(snapAngle(300, 0), 0);        // exactly 60° away: still within hysteresis
  assert.equal(snapAngle(290, 0), 270);
  assert.equal(snapAngle(null, 90), 90);     // flat: keep last
});

test('frame rotation is the device turn beyond the screen', () => {
  assert.equal(frameRotation(null, 0), 0);   // no sensor (laptop): never rotate
  assert.equal(frameRotation(0, 0), 0);
  assert.equal(frameRotation(90, 90), 0);    // screen follows the phone: browser already upright
  assert.equal(frameRotation(90, 0), 90);    // rotation lock: portrait screen, phone in landscape
  assert.equal(frameRotation(270, 0), 270);
  assert.equal(frameRotation(0, 90), 270);
  assert.equal(normalize(-90), 270);
});

test('smoothed gravity converges without angle wrap issues', () => {
  let g = smoothGravity(null, 0, G);
  for (let i = 0; i < 60; i++) g = smoothGravity(g, G * Math.sin(Math.PI / 6), G * Math.cos(Math.PI / 6));
  assert.equal(gravityAngle(g.x, g.y), 30);
  assert.equal(smoothGravity(g, null, 1), g);  // missing reading keeps the last value
});

test('tilt is what is left after the 90° step', () => {
  assert.equal(tiltFrom(30, 0), 30);
  assert.equal(tiltFrom(340, 0), -20);
  assert.equal(tiltFrom(100, 90), 10);
  assert.equal(tiltFrom(55, 90), -35);
  assert.equal(tiltFrom(null, 0), 0);
});

test('tilt dead zone with hysteresis', () => {
  assert.deepEqual(tiltDeadzone(8, false), {tilt: 0, active: false});   // small tilt ignored
  assert.deepEqual(tiltDeadzone(12, false), {tilt: 12, active: true});
  assert.deepEqual(tiltDeadzone(8, true), {tilt: 8, active: true});     // keeps correcting until < 6
  assert.deepEqual(tiltDeadzone(-5, true), {tilt: 0, active: false});
});

test('crop zoom keeps the output filled', () => {
  assert.equal(cropZoom(960, 720, 0), 1);
  assert.ok(Math.abs(cropZoom(100, 100, 45) - Math.SQRT2) < 1e-9);       // square at 45°: √2
  const k = cropZoom(960, 720, 15);
  // every corner of the output box, mapped back into the turned frame, stays inside it
  const r = 15 * Math.PI / 180;
  for (const [x, y] of [[480, 360], [-480, 360]]) {
    const u = (x * Math.cos(r) + y * Math.sin(r)) / k, v = (-x * Math.sin(r) + y * Math.cos(r)) / k;
    assert.ok(Math.abs(u) <= 480 + 1e-6 && Math.abs(v) <= 360 + 1e-6);
  }
  assert.equal(cropZoom(960, 720, -15), k);
});

test('upright layout swaps canvas sides for quarter turns', () => {
  assert.deepEqual(uprightLayout(960, 540, 0), {canvasWidth: 960, canvasHeight: 540, radians: -0, zoom: 1});
  const tilted = uprightLayout(960, 720, 0, 20);
  assert.equal(tilted.canvasWidth, 960);                                  // tilt never changes the size
  assert.equal(tilted.radians, -20 * Math.PI / 180);
  assert.equal(tilted.zoom, cropZoom(960, 720, 20));
  assert.equal(uprightLayout(960, 720, 90, -10).canvasWidth, 720);
  const quarter = uprightLayout(540, 960, 90);
  assert.equal(quarter.canvasWidth, 960);
  assert.equal(quarter.canvasHeight, 540);
  assert.equal(quarter.radians, -Math.PI / 2);
  assert.equal(uprightLayout(960, 540, 180).canvasWidth, 960);
});
