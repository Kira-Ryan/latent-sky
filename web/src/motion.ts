/**
 * prefers-reduced-motion — the single gate every scripted animation checks.
 *
 * Framework-free and Cesium-free, importable from ui/ and globe/ alike: the
 * auto-sweep uses it today, and the planned camera fly-down reuses it. CSS
 * transitions are gated separately by the media query in app.css; this helper
 * is for animation driven from JavaScript (rAF loops, camera flights).
 *
 * Queried live at each call — the OS setting can change while the page is open.
 */
export function motionOk(): boolean {
  return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
