import { defineConfig, type Plugin } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { viteStaticCopy } from "vite-plugin-static-copy";
import { fileURLToPath } from "node:url";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { createReadStream, statSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));

// Deploy base. Overridable for sub-path deploys: LATENT_SKY_BASE=/latent-sky/ vite build
const base = (() => {
  let b = process.env.LATENT_SKY_BASE ?? "/";
  if (!b.endsWith("/")) b += "/";
  return b;
})();

const MIME: Record<string, string> = {
  ".json": "application/json",
  ".webp": "image/webp",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".xml": "application/xml",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".css": "text/css",
  ".wasm": "application/wasm",
  ".ktx2": "image/ktx2",
  ".glb": "model/gltf-binary",
  ".terrain": "application/octet-stream",
};

/** Dev-only static file middleware mounted at urlPrefix, serving from fsRoot. */
function serveStatic(name: string, urlPrefix: string, fsRoot: string): Plugin {
  return {
    name,
    configureServer(server) {
      server.middlewares.use(urlPrefix, (req, res) => {
        const urlPath = decodeURIComponent((req.url ?? "/").split("?")[0]);
        const file = normalize(join(fsRoot, urlPath));
        if (!file.startsWith(fsRoot)) {
          res.statusCode = 403;
          res.end("forbidden");
          return;
        }
        let stat;
        try {
          stat = statSync(file);
        } catch {
          res.statusCode = 404;
          res.end(`not found: ${urlPrefix}${urlPath}`);
          return;
        }
        if (!stat.isFile()) {
          res.statusCode = 404;
          res.end(`not a file: ${urlPrefix}${urlPath}`);
          return;
        }
        res.setHeader("content-type", MIME[extname(file).toLowerCase()] ?? "application/octet-stream");
        createReadStream(file).pipe(res);
      });
    },
  };
}

/**
 * Dev serving of /data/* from ../data/* so ?manifest=/data/dev/encoded/manifest.json
 * works against real dev data. data/dev is CC BY-NC-ND-derived and gitignored —
 * served locally, never built in.
 */
function serveRepoData(): Plugin {
  return serveStatic("latent-sky-serve-repo-data", "/data", resolve(here, "..", "data"));
}

/**
 * Dev serving of /cesium/* (Workers, Assets, ThirdParty) straight from the
 * cesium package. vite-plugin-static-copy only copies at BUILD time — without
 * this, every runtime asset request falls through to the SPA index.html with a
 * 200 and Cesium fails with cryptic JSON parse errors.
 */
function serveCesiumStatic(): Plugin {
  return serveStatic(
    "latent-sky-serve-cesium-static",
    "/cesium",
    resolve(here, "node_modules", "cesium", "Build", "Cesium"),
  );
}

export default defineConfig({
  base,
  define: {
    // Base-aware, per Architecture.md §10. NEVER a hardcoded root-absolute
    // JSON.stringify("/cesiumStatic") — that 404s every Worker and Asset on a
    // sub-path deploy and renders a black sphere with cryptic console errors.
    CESIUM_BASE_URL: JSON.stringify(`${base}cesium/`),
  },
  plugins: [
    svelte(),
    viteStaticCopy({
      targets: [
        // NOT Widgets — CesiumWidget carries no stock chrome and widgets.css never ships (§6.4).
        { src: "node_modules/cesium/Build/Cesium/Workers/*", dest: "cesium/Workers" },
        { src: "node_modules/cesium/Build/Cesium/Assets/*", dest: "cesium/Assets" },
        { src: "node_modules/cesium/Build/Cesium/ThirdParty/*", dest: "cesium/ThirdParty" },
      ],
    }),
    serveRepoData(),
    serveCesiumStatic(),
  ],
  build: {
    target: "es2022",
    // Cesium is a single ~4 MB minified chunk pre-brotli; the payload gate is §8's CI job, not this warning.
    chunkSizeWarningLimit: 4500,
  },
  server: {
    port: 5180,
  },
});
