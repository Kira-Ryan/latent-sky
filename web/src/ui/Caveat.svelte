<script lang="ts">
  import { sky } from "../state/store.svelte";
</script>

<!-- The caveat lives in the UI, not just the README (§13). Always visible,
     never a tooltip — styled as a legible footnote: a hairline rule, a quiet
     small-caps heading, comfortable measure. The contact byline shares the
     strip: same register, right-aligned, wrapping below on narrow screens. -->
<div class="caveat">
  <div>
    <span class="caveat-label">About this data</span>
    <p class="caveat-text">
      {sky.manifest?.run.generatedNote}
      {#if sky.manifest?.run.reportUrl}
        <!-- The numbers behind the pictures: the run's verification against
             observations, published beside the site rather than claimed here. -->
        <a class="report" href={sky.manifest.run.reportUrl} target="_blank" rel="noopener noreferrer">
          How well did it do? Read the verification.
        </a>
      {/if}
    </p>
  </div>
  <address class="contact">
    <span class="caveat-label">Built by</span>
    <p class="caveat-text">
      Kira Ryan
      <span class="sep" aria-hidden="true">·</span>
      <a href="mailto:KiraRyan27@gmail.com">KiraRyan27@gmail.com</a>
      <span class="sep" aria-hidden="true">·</span>
      <a href="https://www.linkedin.com/in/kira-ryan/" target="_blank" rel="noopener noreferrer">LinkedIn</a>
    </p>
  </address>
</div>

<style>
  .caveat {
    margin: 9px 0 0;
    padding-top: 7px;
    border-top: 1px solid rgba(44, 61, 99, 0.55);
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px 40px;
    flex-wrap: wrap;
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

  /* address defaults to italic; this is a footnote, not a quotation. */
  .contact {
    font-style: normal;
    text-align: right;
    flex-shrink: 0;
  }

  .contact .caveat-text {
    white-space: nowrap;
  }

  .report {
    color: var(--text-dim);
    text-decoration: none;
    border-bottom: 1px solid rgba(148, 163, 184, 0.35);
    white-space: nowrap;
  }

  .report:hover,
  .report:focus-visible {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .contact a {
    color: var(--text-dim);
    text-decoration: none;
    border-bottom: 1px solid rgba(148, 163, 184, 0.35);
  }

  .contact a:hover,
  .contact a:focus-visible {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .sep {
    color: var(--text-faint);
    margin: 0 2px;
  }

  /* When the row wraps (narrow viewports), the byline reads left like the rest. */
  @media (max-width: 900px) {
    .contact {
      text-align: left;
    }
  }
</style>
