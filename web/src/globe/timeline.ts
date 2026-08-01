/**
 * Clock -> frame index + fraction — Architecture.md §6.2.
 *
 * Frames are the manifest's valid times, not necessarily uniformly spaced
 * (dev-sample frames are independent analysis times). The scrubber and the
 * cross-fade both work in fractional frame index space; this module converts
 * between that space and the Cesium clock's JulianDate space.
 */
import { ClockRange, ClockStep, JulianDate, type Clock } from "cesium";

/** Wall seconds one frame interval should take at speed 1. */
const SECONDS_PER_FRAME = 1.5;

export class Timeline {
  readonly frames: JulianDate[];
  readonly baseMultiplier: number;

  constructor(
    readonly clock: Clock,
    frameIso: string[],
  ) {
    if (frameIso.length < 1) throw new Error("timeline needs at least one frame");
    this.frames = frameIso.map((iso) => JulianDate.fromIso8601(iso));

    const n = this.frames.length;
    if (n > 1) {
      const intervals = this.frames
        .slice(1)
        .map((t, i) => JulianDate.secondsDifference(t, this.frames[i]))
        .sort((a, b) => a - b);
      const median = intervals[Math.floor(intervals.length / 2)];
      this.baseMultiplier = median / SECONDS_PER_FRAME;
    } else {
      this.baseMultiplier = 1;
    }

    clock.startTime = this.frames[0].clone();
    clock.stopTime = this.frames[n - 1].clone();
    clock.currentTime = this.frames[0].clone();
    clock.clockRange = n > 1 ? ClockRange.LOOP_STOP : ClockRange.CLAMPED;
    clock.clockStep = ClockStep.SYSTEM_CLOCK_MULTIPLIER;
    clock.multiplier = this.baseMultiplier;
    clock.shouldAnimate = false;
  }

  get frameCount(): number {
    return this.frames.length;
  }

  /** Clock time -> fractional frame index, clamped to [0, n-1]. */
  frameFloatFor(time: JulianDate): number {
    const frames = this.frames;
    const n = frames.length;
    if (n === 1 || JulianDate.lessThanOrEquals(time, frames[0])) return 0;
    if (JulianDate.greaterThanOrEquals(time, frames[n - 1])) return n - 1;
    let i = 0;
    while (i < n - 2 && JulianDate.greaterThanOrEquals(time, frames[i + 1])) i++;
    const span = JulianDate.secondsDifference(frames[i + 1], frames[i]);
    const into = JulianDate.secondsDifference(time, frames[i]);
    return i + into / span;
  }

  /** Fractional frame index -> clock time. */
  timeForFrameFloat(f: number): JulianDate {
    const frames = this.frames;
    const n = frames.length;
    if (n === 1) return frames[0].clone();
    const clamped = Math.min(Math.max(f, 0), n - 1);
    const i = Math.min(Math.floor(clamped), n - 2);
    const frac = clamped - i;
    const span = JulianDate.secondsDifference(frames[i + 1], frames[i]);
    return JulianDate.addSeconds(frames[i], frac * span, new JulianDate());
  }

  /** Fractional frame index -> integer index + cross-fade fraction. */
  split(f: number): { i: number; frac: number } {
    const n = this.frames.length;
    if (n === 1) return { i: 0, frac: 0 };
    const clamped = Math.min(Math.max(f, 0), n - 1);
    const i = Math.min(Math.floor(clamped), n - 2);
    return { i, frac: clamped - i };
  }

  /** ISO string of the nearest frame — the scrubber's aria-valuetext. */
  nearestIso(f: number, frameIso: string[]): string {
    const i = Math.min(Math.max(Math.round(f), 0), frameIso.length - 1);
    return frameIso[i];
  }
}
