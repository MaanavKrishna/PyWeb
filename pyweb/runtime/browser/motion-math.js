// PyWeb motion: the maths. Every animation is a function of time with a known value and
// velocity at any moment, so an animation interrupted halfway hands its velocity to the next one.
// No DOM here (tested in Node).

/** A damped spring from `from` to `to` (velocity in units per second). */
export function spring({ from = 0, to = 1, velocity = 0, stiffness = 100, damping = 10, mass = 1,
  restDelta, restSpeed } = {}) {
  const x0 = from - to;
  const w0 = Math.sqrt(stiffness / mass);
  const zeta = damping / (2 * Math.sqrt(stiffness * mass));
  let pos;
  if (zeta < 1) {
    const wd = w0 * Math.sqrt(1 - zeta * zeta);
    const b = (velocity + zeta * w0 * x0) / wd;
    pos = (t) => Math.exp(-zeta * w0 * t) * (x0 * Math.cos(wd * t) + b * Math.sin(wd * t));
  } else if (zeta === 1) {
    const b = velocity + w0 * x0;
    pos = (t) => Math.exp(-w0 * t) * (x0 + b * t);
  } else {
    const s = w0 * Math.sqrt(zeta * zeta - 1);
    const r1 = -zeta * w0 + s, r2 = -zeta * w0 - s;
    const b = (velocity - r1 * x0) / (r2 - r1), a = x0 - b;
    pos = (t) => a * Math.exp(r1 * t) + b * Math.exp(r2 * t);
  }
  const span = Math.abs(to - from);
  const rd = restDelta ?? Math.max(span * 0.001, 1e-3);
  const rs = restSpeed ?? Math.max(span * 0.02, 1e-2);
  const vel = (t) => { const lo = Math.max(0, t - 1e-4), hi = t + 1e-4; return (pos(hi) - pos(lo)) / (hi - lo); };
  let duration = 0;
  while (duration < 10 && !(Math.abs(pos(duration)) < rd && Math.abs(vel(duration)) < rs)) duration += 1 / 120;
  return {
    duration,
    value: (t) => (t <= 0 ? from : t >= duration ? to : to + pos(t)),
    velocity: (t) => (t <= 0 ? velocity : t >= duration ? 0 : vel(t)),
  };
}

/** Spring settings from a duration (seconds) and bounce (0 none … 1 very bouncy). */
export function springFor(duration = 0.4, bounce = 0.2) {
  const stiffness = (2 * Math.PI / Math.max(duration, 0.05)) ** 2;
  return { stiffness, damping: 2 * (1 - Math.min(Math.max(bounce, 0), 0.95)) * Math.sqrt(stiffness), mass: 1 };
}

/** CSS cubic-bezier(x1, y1, x2, y2) as a function of progress. */
export function bezier(x1, y1, x2, y2) {
  const ax = 3 * x1 - 3 * x2 + 1, bx = 3 * x2 - 6 * x1, cx = 3 * x1;
  const ay = 3 * y1 - 3 * y2 + 1, by = 3 * y2 - 6 * y1, cy = 3 * y1;
  const fx = (t) => ((ax * t + bx) * t + cx) * t;
  const fy = (t) => ((ay * t + by) * t + cy) * t;
  return (p) => {
    if (p <= 0 || p >= 1) return p <= 0 ? 0 : 1;
    let t = p;
    for (let i = 0; i < 8; i++) {
      const d = (3 * ax * t + 2 * bx) * t + cx;
      if (Math.abs(d) < 1e-6) break;
      t -= (fx(t) - p) / d;
    }
    t = Math.min(Math.max(t, 0), 1);
    return fy(t);
  };
}

export const EASINGS = {
  linear: [0, 0, 1, 1], ease: [0.25, 0.1, 0.25, 1], "ease-in": [0.42, 0, 1, 1], "ease-out": [0, 0, 0.58, 1],
  "ease-in-out": [0.42, 0, 0.58, 1], "circ-out": [0, 0.55, 0.45, 1], "back-out": [0.34, 1.56, 0.64, 1],
  "back-in": [0.36, 0, 0.66, -0.56], anticipate: [0.68, -0.6, 0.32, 1.6],
};

export function easing(e) {
  const p = Array.isArray(e) ? e : EASINGS[String(e || "ease-out").replace(/_/g, "-").replace(/([a-z])([A-Z])/g, "$1-$2").toLowerCase()];
  if (!p) return (x) => x;
  if (p === EASINGS.linear) return (x) => x;
  return bezier(...p);
}

/** A tween through `values` (two or more) over `duration` seconds. */
export function tween({ values, duration = 0.3, ease = "ease-out", times } = {}) {
  const n = values.length - 1;
  const at = times && times.length === values.length ? times : values.map((_v, i) => i / Math.max(n, 1));
  const eases = Array.isArray(ease) && Array.isArray(ease[0]) || (Array.isArray(ease) && typeof ease[0] === "string")
    ? ease.map(easing) : null;
  const one = eases ? null : easing(ease);
  const value = (t) => {
    if (t <= 0 || duration <= 0) return t <= 0 ? values[0] : values[n];
    const p = Math.min(t / duration, 1);
    let i = 0;
    while (i < n - 1 && p > at[i + 1]) i++;
    const span = at[i + 1] - at[i] || 1;
    const local = (p - at[i]) / span;
    const k = (eases ? eases[i] || eases[0] : one)(Math.min(Math.max(local, 0), 1));
    return values[i] + (values[i + 1] - values[i]) * k;
  };
  return {
    duration,
    value,
    velocity: (t) => (t <= 0 || t >= duration ? 0 : (value(t + 1e-3) - value(t - 1e-3)) / 2e-3),
  };
}

/** Coasting after a throw: slows down exponentially (and springs to `min`/`max` if it'd pass them). */
export function inertia({ from = 0, velocity = 0, power = 0.8, timeConstant = 0.35, min, max,
  bounceStiffness = 500, bounceDamping = 40, restDelta = 0.5 } = {}) {
  const amplitude = power * velocity * timeConstant;
  const ideal = from + amplitude;
  if ((min !== undefined && ideal < min) || (max !== undefined && ideal > max)) {
    return spring({ from, to: ideal < min ? min : max, velocity, stiffness: bounceStiffness, damping: bounceDamping });
  }
  const pos = (t) => ideal - amplitude * Math.exp(-t / timeConstant);
  let duration = 0;
  while (duration < 10 && Math.abs(amplitude * Math.exp(-duration / timeConstant)) > restDelta) duration += 1 / 120;
  return {
    duration,
    value: (t) => (t <= 0 ? from : t >= duration ? ideal : pos(t)),
    velocity: (t) => (t <= 0 ? velocity : t >= duration ? 0 : (amplitude / timeConstant) * Math.exp(-t / timeConstant)),
  };
}

/** Values of `gen` sampled every frame (60 per second, at least two). */
export function sample(gen, from = 0, to = gen.duration, fps = 60) {
  const n = Math.max(2, Math.min(600, Math.ceil((to - from) * fps) + 1));
  const out = [];
  for (let i = 0; i < n; i++) out.push(gen.value(from + ((to - from) * i) / (n - 1)));
  return out;
}

/** Delays for `count` children: stagger(0.05) → 0, 0.05, 0.1 … (from "first", "last" or "center"). */
export function stagger(each = 0.05, { from = "first", start = 0 } = {}) {
  return (i, count) => {
    const origin = from === "last" ? count - 1 : from === "center" ? (count - 1) / 2 : typeof from === "number" ? from : 0;
    return start + Math.abs(i - origin) * each;
  };
}
