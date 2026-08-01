/// <reference types="vite/client" />

// .svelte imports from .ts modules (main.ts dynamically imports NoWebGL.svelte
// before anything Cesium loads). Imports between .svelte files are resolved by
// the Svelte language tooling directly; this wildcard is the .ts fallback.
declare module "*.svelte" {
  import type { Component } from "svelte";
  const component: Component<Record<string, never>>;
  export default component;
}
