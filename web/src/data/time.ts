/**
 * Time, said the same way everywhere.
 *
 * Three rules this file exists to enforce:
 *
 *  1. ONE CLOCK. The masthead's issue time and the scrubber's valid time are the
 *     same kind of instant, so they are formatted by one function and read as
 *     comparable. Two formatters drift.
 *
 *  2. A RELATIVE CLAIM CAN GO STALE, AN ABSOLUTE ONE CANNOT. The site is static
 *     and behind a CDN, so a visitor may hold a page whose data is much older
 *     than the moment it was published. "5 h ago" is therefore computed in the
 *     browser from the manifest's own instant, never baked at publish time, and
 *     it is dropped entirely past AGE_LIMIT_H rather than growing into a number
 *     nobody reads. The absolute date always carries the meaning on its own.
 *
 *  3. FRAME ZERO IS NOT A FORECAST. It is the analysis the model started from.
 *     Labelling it "+0 h" would let someone screenshot observed initial
 *     conditions as model output, so it is named.
 */

/** Beyond this, an age in hours stops informing and the absolute date carries it. */
const AGE_LIMIT_H = 48;

const UTC_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

/** "02 Sept 2026, 22:00 UTC" — the one instant format the whole UI uses. */
export function formatUtc(iso: string | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  return `${UTC_FORMAT.format(new Date(t))} UTC`;
}

/**
 * "5 h ago", or "" when the answer would be unhelpful or untrue: no instant, an
 * unparseable one, an instant in the future (a skewed visitor clock), or one
 * older than AGE_LIMIT_H. Whole hours only, so there is no day/plural logic to
 * get wrong, and under an hour reads as "just now" rather than "0 h ago".
 */
export function ageLabel(iso: string | undefined, nowMs: number): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const hours = (nowMs - t) / 3_600_000;
  if (hours < 0) return "";
  if (hours < 1) return "just now";
  if (hours > AGE_LIMIT_H) return "";
  return `${Math.floor(hours)} h ago`;
}

/**
 * How far ahead of its initialisation a frame looks: "+10 h", or "analysis" for
 * frame zero. Empty when the run declares no init (the ERA5 event's frames are
 * independent analysis times and have no lead), or when the frame precedes it.
 *
 * Fractional leads keep one decimal so a sub-hourly cadence is never rounded
 * into a whole number it does not have.
 */
export function leadLabel(initIso: string | undefined, frameIso: string | undefined): string {
  if (!initIso || !frameIso) return "";
  const init = Date.parse(initIso);
  const frame = Date.parse(frameIso);
  if (Number.isNaN(init) || Number.isNaN(frame)) return "";
  const hours = (frame - init) / 3_600_000;
  if (hours < 0) return "";
  if (hours === 0) return "analysis";
  return `+${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
}

/** The same thing spelled out, for screen readers and title attributes. */
export function leadSpoken(initIso: string | undefined, frameIso: string | undefined): string {
  const label = leadLabel(initIso, frameIso);
  if (!label) return "";
  if (label === "analysis") return "the analysis the forecast started from";
  return `${label.slice(1)} after initialisation`;
}
