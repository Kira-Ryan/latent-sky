<script lang="ts">
  import { sky } from "../state/store.svelte";
</script>

<!-- The caveat lives in the UI, not just the README (§13). Always visible,
     never a tooltip — styled as a legible footnote: a hairline rule, a quiet
     small-caps heading, comfortable measure. -->
<div class="caveat">
  <span class="caveat-label">About this data</span>
  <p class="caveat-text">{sky.manifest?.run.generatedNote}</p>
</div>

<style>
  .caveat {
    margin: 9px 0 0;
    padding-top: 7px;
    border-top: 1px solid rgba(44, 61, 99, 0.55);
  }

  .caveat-label {
    display: block;
    margin-bottom: 2px;
    font-size: 9.5px;
    font-weight: 600;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--text-faint);
  }

  .caveat-text {
    margin: 0;
    font-size: 11px;
    line-height: 1.55;
    color: var(--text-faint);
    max-width: 116ch;
  }
</style>
