/**
 * Entry point. The WebGL2 probe runs on a raw canvas BEFORE anything Cesium is
 * imported or constructed (§10) — every import below is dynamic, so failing the
 * probe never even parses the Cesium bundle.
 */
import "./app.css";

const target = document.getElementById("app");
if (!target) throw new Error("no #app element");

async function start(): Promise<void> {
  let gl: WebGL2RenderingContext | null = null;
  try {
    gl = document.createElement("canvas").getContext("webgl2");
  } catch {
    gl = null;
  }

  if (!gl) {
    const [{ mount }, { default: NoWebGL }] = await Promise.all([
      import("svelte"),
      import("./ui/NoWebGL.svelte"),
    ]);
    mount(NoWebGL, { target: target! });
    return;
  }
  gl.getExtension("WEBGL_lose_context")?.loseContext();

  const { boot } = await import("./boot");
  await boot(target!);
}

start().catch((err: unknown) => {
  // Surface loudly: on screen AND rethrown so it lands in the console as an
  // unhandled rejection. Never swallowed.
  const pre = document.createElement("pre");
  pre.className = "boot-error";
  pre.textContent = `Latent Sky failed to start:\n\n${err instanceof Error ? (err.stack ?? err.message) : String(err)}`;
  target!.appendChild(pre);
  throw err;
});
