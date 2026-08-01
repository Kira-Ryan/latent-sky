<script lang="ts">
  import { sky } from "../state/store.svelte";
</script>

<!-- The caveat lives in the UI, not just the README (§13). Always visible,
     never a tooltip. -->
<p class="caveat">{sky.manifest?.run.generatedNote}</p>

<style>
  .caveat {
    margin: 8px 0 0;
    font-size: 11px;
    color: var(--text-dim);
    max-width: 110ch;
  }
</style>
