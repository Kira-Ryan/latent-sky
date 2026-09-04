<script lang="ts">
  import { sky } from "../state/store.svelte";

  // Where this run stands in the scoring loop. Undefined is a real answer and
  // means "nothing is scoring this", which is the truth for a typhoon outside
  // radar coverage — so the whole line is absent rather than reassuring.
  const state = $derived(sky.manifest?.run.verification);
  const report = $derived(sky.manifest?.run.reportUrl);

  /**
   * The measured result, in a sentence, so a visitor who never opens the report
   * still sees that the claim was tested and how it went. Every number comes
   * from the scorer's own output; nothing here is typed.
   *
   * The wording leads with the limitation rather than the achievement, because
   * that is what the figure actually says: skill at a ~100 km neighbourhood is
   * mesoscale placement, not storm-scale, and a reader who takes "useful skill"
   * as "it got the storms right" has been misled by omission.
   */
  const measured = $derived.by(() => {
    const s = sky.manifest?.run.verificationSummary;
    if (!s) return "";
    const hours = `${s.usefulHours} of ${s.scoredHours} hours`;
    if (s.usefulScaleKm === null) {
      return `No useful skill at ${s.thresholdDbz} dBZ at any scale up to ${Math.round(s.largestScaleKm)} km.`;
    }
    return `Useful skill at ${s.thresholdDbz} dBZ only at ${Math.round(s.usefulScaleKm)} km neighbourhoods, ${hours}.`;
  });
</script>

<!-- The caveat lives in the UI, not just the README (§13). Always visible,
     never a tooltip — styled as a legible footnote: a hairline rule, a quiet
     small-caps heading, comfortable measure. The contact byline shares the
     strip: same register, right-aligned, wrapping below on narrow screens.

     The verification line sits ABOVE all of it and one step louder. That a
     forecast is scored against radar and the result published whatever it says
     is the most interesting true thing on this site, and it was previously
     buried mid-paragraph in an 11px footnote nobody reads. It is still prose,
     not a badge: a status pill would have to stay accurate on a page a CDN
     served from cache a day later, and this sentence does. -->
{#if state}
  <p class="verification">
    {#if state === "scored"}
      <strong>Scored against MRMS radar.</strong>
      {#if measured}<span class="measured">{measured}</span>{/if}
      {#if report}
        <a href={report} target="_blank" rel="noopener noreferrer">Read the verification.</a>
      {/if}
    {:else}
      <strong>Not yet scored.</strong>
      Published before the weather happened, and measured against radar afterwards.
    {/if}
  </p>
{/if}

<div class="caveat" class:under-verification={state !== undefined}>
  <div>
    <span class="caveat-label">About this data</span>
    <p class="caveat-text">{sky.manifest?.run.generatedNote}</p>
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
  .verification {
    margin: 9px 0 0;
    padding-top: 7px;
    border-top: 1px solid rgba(44, 61, 99, 0.55);
    font-size: 12px;
    line-height: 1.5;
    color: var(--text-dim);
    max-width: 116ch;
  }

  .verification strong {
    font-weight: 600;
    color: var(--text);
  }

  /* The measured figure sits between the claim and the link, in tabular
     numerals so the digits do not wobble when the event changes. */
  .measured {
    font-variant-numeric: tabular-nums;
  }

  .verification a {
    color: var(--accent);
    text-decoration: none;
    border-bottom: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  }

  .verification a:hover,
  .verification a:focus-visible {
    border-bottom-color: var(--accent);
  }

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

  /* When the verification line is present it owns the rule that opens the
     block, so the strip below it does not draw a second one four pixels later. */
  .caveat.under-verification {
    margin-top: 6px;
    padding-top: 0;
    border-top: none;
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
