/** Motion primitives: springs that start from the on-screen value and carry velocity, momentum projection and
 * rubber-banding. Everything a finger can move uses these; nothing gesture-driven uses CSS transitions, which
 * cannot be grabbed mid-flight. */

export interface SpringParams {
  /** 1 settles without overshoot; below 1 overshoots. Use < 1 only after a gesture that carried momentum. */
  damping: number;
  /** Seconds to (nearly) reach the target. Not a duration: the settle time follows from the parameters. */
  response: number;
}

export const SETTLE: SpringParams = { damping: 1, response: 0.35 };
export const FLICK: SpringParams = { damping: 0.8, response: 0.3 };

export interface SpringState {
  value: number;
  velocity: number; // units per second
}

const SUBSTEP_S = 1 / 240;

/** Advance a damped spring by ``dt`` seconds (semi-implicit Euler in fixed substeps, stable at any frame rate). */
export function stepSpring(s: SpringState, target: number, p: SpringParams, dt: number): SpringState {
  const omega = (2 * Math.PI) / p.response;
  const k = omega * omega;
  const c = 2 * p.damping * omega;
  let { value, velocity } = s;
  for (let t = 0; t < dt; t += SUBSTEP_S) {
    const h = Math.min(SUBSTEP_S, dt - t);
    velocity += (-k * (value - target) - c * velocity) * h;
    value += velocity * h;
  }
  return { value, velocity };
}

export function settled(s: SpringState, target: number): boolean {
  return Math.abs(s.value - target) < 0.5 && Math.abs(s.velocity) < 5;
}

/** Where a flick would come to rest, with the deceleration of a scroll view. */
export function project(velocity: number, decelerationRate = 0.998): number {
  return ((velocity / 1000) * decelerationRate) / (1 - decelerationRate);
}

/** Past a boundary the element follows less and less: real things slow before they stop. */
export function rubberband(overshoot: number, dimension: number, constant = 0.55): number {
  const sign = Math.sign(overshoot);
  const o = Math.abs(overshoot);
  return (sign * (o * dimension * constant)) / (dimension + constant * o);
}

/** Pointer velocity from the last ``windowMs`` of samples, in units per second. */
export function velocityOf(samples: { t: number; x: number }[], windowMs = 100): number {
  if (samples.length < 2) return 0;
  const last = samples[samples.length - 1]!;
  const first = samples.find((s) => last.t - s.t <= windowMs) ?? samples[0]!;
  const dt = last.t - first.t;
  return dt > 0 ? ((last.x - first.x) / dt) * 1000 : 0;
}

export function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** A spring driven by animation frames. ``to`` retargets from the current value and velocity, so a moving
 * element can be redirected at any instant without a jump or a velocity "wall". */
export class Spring {
  state: SpringState;
  private target: number;
  private params: SpringParams = SETTLE;
  private frame = 0;
  private last = 0;

  constructor(
    value: number,
    private readonly onUpdate: (value: number) => void,
    private readonly raf: (cb: (t: number) => void) => number = (cb) => requestAnimationFrame(cb),
    private readonly caf: (id: number) => void = (id) => cancelAnimationFrame(id),
  ) {
    this.state = { value, velocity: 0 };
    this.target = value;
  }

  get value(): number {
    return this.state.value;
  }

  /** Track a value directly (a finger on the element): stops any animation, keeps the latest velocity. */
  set(value: number, velocity = 0): void {
    this.stop();
    this.state = { value, velocity };
    this.target = value;
    this.onUpdate(value);
  }

  to(target: number, params: SpringParams = SETTLE, velocity?: number): void {
    this.target = target;
    this.params = params;
    if (velocity !== undefined) this.state = { ...this.state, velocity };
    if (prefersReducedMotion()) return this.set(target);
    if (!this.frame) {
      this.last = 0;
      this.frame = this.raf(this.tick);
    }
  }

  stop(): void {
    if (this.frame) this.caf(this.frame);
    this.frame = 0;
  }

  private tick = (t: number): void => {
    const dt = this.last ? Math.min((t - this.last) / 1000, 1 / 20) : 1 / 60;
    this.last = t;
    this.state = stepSpring(this.state, this.target, this.params, dt);
    if (settled(this.state, this.target)) {
      this.state = { value: this.target, velocity: 0 };
      this.frame = 0;
      this.onUpdate(this.target);
      return;
    }
    this.onUpdate(this.state.value);
    this.frame = this.raf(this.tick);
  };
}

/** A short vibration on the same frame as the visual change it belongs to. Only for completion and errors. */
export function haptic(kind: "success" | "error"): void {
  try {
    navigator.vibrate?.(kind === "success" ? 12 : [18, 60, 18]);
  } catch {
    /* not supported: the visual feedback carries it */
  }
}
