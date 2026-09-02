<script lang="ts">
  /**
   * The event switcher — a quiet control in the masthead listing the hero
   * events the catalogue declares.
   *
   * It renders NOTHING when the catalogue holds fewer than two events, which is
   * the state the site deploys in today: one event is not a choice, and a
   * disabled or single-item menu is chrome that explains nothing.
   *
   * Pattern: a collapsed button + listbox popup (WAI-ARIA "select-only
   * combobox" shape, minus the textbox). Focus moves into the list on open,
   * arrows/Home/End rove it, Enter or Space selects, Escape closes and returns
   * focus to the trigger. Escape is stopped here so it never also reaches
   * App's window handler and flies the camera back to orbit.
   */
  import { tick } from "svelte";
  import { sky } from "../state/store.svelte";

  let { onselect }: { onselect: (id: string) => void } = $props();

  let open = $state(false);
  let rootEl = $state<HTMLDivElement | undefined>(undefined);
  let triggerEl = $state<HTMLButtonElement | undefined>(undefined);
  let optionEls = $state<(HTMLButtonElement | undefined)[]>([]);

  const activeIndex = $derived(
    Math.max(
      sky.events.findIndex((e) => e.id === sky.activeEventId),
      0,
    ),
  );
  const activeTitle = $derived(sky.activeEvent?.title ?? "Choose an event");

  function focusOption(i: number): void {
    const n = sky.events.length;
    if (n === 0) return;
    optionEls[((i % n) + n) % n]?.focus();
  }

  async function openList(): Promise<void> {
    open = true;
    await tick(); // the options do not exist until the popup has rendered
    focusOption(activeIndex);
  }

  function closeList(refocusTrigger = true): void {
    if (!open) return;
    open = false;
    if (refocusTrigger) triggerEl?.focus();
  }

  function select(id: string): void {
    closeList();
    if (sky.switching || id === sky.activeEventId) return;
    onselect(id);
  }

  function onTriggerKeydown(event: KeyboardEvent): void {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) void openList();
      else focusOption(event.key === "ArrowDown" ? 0 : sky.events.length - 1);
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation(); // never fall through to App's return-to-orbit
      closeList();
    }
  }

  function onListKeydown(event: KeyboardEvent): void {
    const from = optionEls.findIndex((el) => el !== undefined && el === document.activeElement);
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        focusOption(from + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        focusOption(from - 1);
        break;
      case "Home":
        event.preventDefault();
        focusOption(0);
        break;
      case "End":
        event.preventDefault();
        focusOption(sky.events.length - 1);
        break;
      case "Escape":
        event.preventDefault();
        event.stopPropagation(); // never fall through to App's return-to-orbit
        closeList();
        break;
      case "Tab":
        closeList(false); // let focus continue on its way out
        break;
      default:
    }
  }

  // Dismiss on any press outside the control. Capture phase, so a press that
  // lands on the globe closes the popup before Cesium starts a camera drag.
  $effect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent): void => {
      if (rootEl && event.target instanceof Node && rootEl.contains(event.target)) return;
      open = false;
    };
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => window.removeEventListener("pointerdown", onPointerDown, true);
  });

  // A catalogue that shrinks to one event (or none) takes the control with it.
  $effect(() => {
    if (!sky.showSwitcher && open) open = false;
  });
</script>

{#if sky.showSwitcher}
  <div class="switcher" bind:this={rootEl}>
    <button
      class="trigger"
      class:busy={sky.switching}
      bind:this={triggerEl}
      aria-haspopup="listbox"
      aria-expanded={open}
      {...open ? { "aria-controls": "event-switcher-list" } : {}}
      aria-label={`Event: ${activeTitle}. Change event`}
      onclick={() => (open ? closeList(false) : void openList())}
      onkeydown={onTriggerKeydown}
    >
      <span class="trigger-title">{activeTitle}</span>
      <svg class="chev" class:up={open} viewBox="0 0 10 6" width="10" height="6" aria-hidden="true">
        <path d="M1 1 L5 5 L9 1" fill="none" stroke="currentColor" stroke-width="1.4" />
      </svg>
    </button>

    {#if open}
      <div
        class="list"
        id="event-switcher-list"
        role="listbox"
        aria-label="Event"
        tabindex="-1"
        onkeydown={onListKeydown}
      >
        {#each sky.events as event, i (event.id)}
          <button
            class="option"
            class:active={event.id === sky.activeEventId}
            bind:this={optionEls[i]}
            role="option"
            aria-selected={event.id === sky.activeEventId}
            tabindex={event.id === sky.activeEventId ? 0 : -1}
            onclick={() => select(event.id)}
          >
            <span class="tick" aria-hidden="true">{event.id === sky.activeEventId ? "•" : ""}</span>
            <span class="option-text">
              <span class="option-title">{event.title}</span>
              {#if event.subtitle}
                <span class="option-subtitle">{event.subtitle}</span>
              {/if}
            </span>
          </button>
        {/each}
      </div>
    {/if}

    <!-- Screen-reader narration of the switch, which is otherwise a silent
         repaint of a canvas. -->
    <span class="vh" aria-live="polite">
      {sky.switching ? `Loading ${activeTitle}` : `Showing ${activeTitle}`}
    </span>
  </div>
{/if}

<style>
  .switcher {
    position: relative;
    pointer-events: auto; /* the masthead itself is pointer-events: none */
  }

  /* Same pill language as the variable picker and the place label — one step
     quieter, because this is navigation, not an instrument reading. */
  .trigger {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    /* Fluid below ~1150px so the masthead cannot grow into the top-right
       stack; the title ellipsises rather than pushing the row wider. */
    max-width: min(300px, 26vw);
    padding: 4px 11px;
    font-size: 11.5px;
    letter-spacing: 0.05em;
    color: var(--text-dim);
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 999px;
    backdrop-filter: blur(6px);
    transition:
      color 0.2s ease,
      border-color 0.2s ease,
      opacity 0.2s ease;
  }

  .trigger:hover,
  .trigger:focus-visible {
    color: var(--text);
    border-color: rgba(79, 209, 197, 0.5);
  }

  .trigger.busy {
    opacity: 0.55;
  }

  .trigger-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .chev {
    flex: none;
    opacity: 0.75;
    transition: transform 0.18s ease;
  }

  .chev.up {
    transform: rotate(180deg);
  }

  .list {
    position: absolute;
    top: calc(100% + 7px);
    left: 0;
    z-index: 6; /* above the invitation (5) — a menu must never be occluded */
    min-width: 268px;
    max-width: min(360px, calc(100vw - 48px));
    padding: 4px;
    background: var(--panel-solid);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55);
  }

  .option {
    display: flex;
    align-items: baseline;
    gap: 8px;
    width: 100%;
    padding: 7px 9px;
    text-align: left;
    border-radius: 5px;
  }

  .option:hover {
    background: rgba(79, 209, 197, 0.07);
  }

  .tick {
    flex: none;
    width: 8px;
    color: var(--accent);
    font-size: 13px;
    line-height: 1;
  }

  .option-text {
    display: block;
    min-width: 0;
  }

  .option-title {
    display: block;
    font-size: 12.5px;
    color: var(--text-dim);
  }

  .option.active .option-title {
    color: var(--accent);
  }

  .option-subtitle {
    display: block;
    margin-top: 1px;
    font-size: 10.5px;
    line-height: 1.4;
    color: var(--text-faint);
  }

  .vh {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
