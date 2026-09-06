# Latent Sky: adversarial review implementation plan

Prepared: 6 September 2026. Review baseline: commit `61d4412`.

Status: proposed implementation. The review and this document do not implement these changes or alter cloud infrastructure.

The objective is to make the displayed weather, verification evidence, and operational state trustworthy when inputs are mismatched, requests fail, or jobs overlap. Preserve the existing static-site architecture and offline GPU inference model.

## Delivery order and dependencies

| Order | Workstream | Priority | Dependency | Completion evidence |
|---|---|---|---|---|
| 1 | Validate forecast and verification provenance | Critical | None; define the contract first | Wrong-run and wrong-grid inputs are rejected before publication |
| 2 | Make GPU ownership and publishing atomic | Critical | Ownership can start immediately; publication uses workstream 1 | Concurrent/replayed jobs preserve ownership, records, and release identity |
| 3 | Make viewer readiness and errors truthful | High | Can proceed alongside workstreams 1 and 2 | Missing imagery produces a visible error; stale requests cannot change the current view |
| 4 | Enforce reproducibility and adversarial checks in CI | High | Establish the CI foundation immediately; add each workstream's tests as it lands | A pull request cannot pass with known regressions or a skipped required artifact |
| 5 | Establish credible scientific baselines | High for public claims | Correct copy immediately; benchmark publication depends on workstreams 1 and 4 | Reproducible comparisons, calibration results, limitations, and complete case accounting |

Each workstream should land in small pull requests with its regression tests. Do not wait until the final CI workstream to add tests. Duration and GPU cost depend on archived-data availability; inventory those inputs before scheduling benchmark runs.

## 1. Bind forecasts, ensemble members, and verification to a validated run

### Problem and evidence

The encoder constructs frame times from the coarse store's initialization and the hero store's lead offsets without first proving they describe the same forecast. The verifier checks ensemble lead offsets but reuses the main hero's coordinates for every member. The encoder attaches an FSS headline without validating the report's run identity.

Synthetic adversarial tests reproduced all three failures:

- Coarse data from 5 September and hero data from 1 September produced a schema-valid manifest labelled 5 September. Different coarse/hero valid-hour spacing was also accepted.
- An ensemble member from March and another with latitude shifted by 10 degrees were accepted by September scoring.
- A March report containing 17 useful hours was attached to a two-frame September run and published in the manifest as scored.

These demonstrate missing validation. They do not establish that the currently published forecasts contain these mismatches.

Existing implementation touchpoints:

- [StormCast encoder](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/src/latentsky/encode_stormcast.py), [Taiwan encoder](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/src/latentsky/encode_forecast.py), and [manifest writer](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/src/latentsky/manifest.py).
- [StormCast forecast writer](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/src/latentsky/forecast_stormcast.py) and [SFNO/CorrDiff forecast writer](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/src/latentsky/forecast.py).
- [Verification CLI](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/tools/verify_fss.py), [scoring functions](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/src/latentsky/verify.py), and [verification index](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/tools/verification_index.py).
- [Manifest schema](C:/Users/User/Desktop/GitHub/latent-sky/schema/manifest.schema.json) and [web manifest loader](C:/Users/User/Desktop/GitHub/latent-sky/web/src/data/manifest.ts).

### Implementation

1. **Define a versioned provenance contract.** Add a shared pipeline module and a versioned schema for a run descriptor and verification results. Record an immutable run ID, model/checkpoint identifiers, configuration digest, source dataset/cycle identifiers, initialization time, valid times, variable names and units, dimensions, and coordinate fingerprints. Record forecast generation and publication times separately from initialization. Distinguish forecast, hindcast, reanalysis, and synthetic data explicitly.

2. **Identify source content reproducibly.** Record checksums for the source archives or their complete file inventories. Define canonical coordinate hashing, including numeric representation, dimension order, longitude convention, and missing-value handling. Do not treat matching filenames or metadata labels as proof that source contents match. Hash inputs once at ingestion and carry the verified descriptor through later stages. Keep forecast-data identity independent of mutable verification/report metadata, so attaching a score does not change the identity it validates or create a circular digest dependency.

3. **Validate each source according to its role.** Derive valid times independently from every store. Reject unexpected initialization, missing/duplicated/non-monotonic times, unit changes, and incompatible shapes. Coarse and fine grids are intentionally different: validate each against its own declared grid and the expected geographic relationship. Require equivalent grids between ensemble members of the same model, after documented coordinate normalization.

4. **Make alignment explicit.** For data with matching cadence, require matching valid-time axes. Where cadence differs legitimately, map or interpolate from actual source timestamps under a declared policy, record that transformation, and reject unsupported intervals. Never infer temporal correspondence from array position or matching lead offsets alone. Audit scoring/report consumers so initialization is not inferred from the first displayed frame and lead hours are calculated from timestamps rather than array indexes. Preserve MRMS timestamp offsets and the permitted observation-matching tolerance.

5. **Validate ensemble membership.** Check every member's run, initialization, valid times, coordinates, variable dimensions, and units before regridding. Require unique member IDs and the expected distinct sampler seeds; do not claim this proves statistical independence. Read each member's own coordinates. Keep the deterministic comparison separate from the ensemble, as the current verifier intends.

6. **Bind scores to exact inputs.** Verification output must identify the forecast descriptor digest, member set, observation source/files, scoring grid, thresholds, neighbourhoods, excluded lead times, and scoring-code version. When attaching a report, validate these references against the forecast being encoded. Derive the summary from validated results; a report URL alone must not establish scored status. Validate useful/scored counts against the scoring policy and actual time coverage, rather than assuming every frame always represents one hour.

7. **Validate before writing output.** Run provenance checks before expensive regridding, image encoding, output replacement, or uploads. Build a complete release in a separate staging directory. Emit the manifest only after validation and referenced-asset checks pass. Use the same validation in the publisher so uploading an archive cannot bypass it.

8. **Migrate readers before writers.** Add web support for the new manifest version while retaining the existing reader. Then update producers and enforce the new contract for newly published runs. Audit legacy releases against their actual source stores; backfill only verifiable metadata. Keep older releases viewable with their provenance limitations visible, without inventing hashes, publication dates, or validation status.

### Regression tests and acceptance criteria

- [ ] Reject the different-day coarse/hero reproduction before any output directory is replaced.
- [ ] Reject a mismatched member initialization, shifted grid, swapped coordinate axis, duplicate member ID, and unexpected variable shape.
- [ ] Accept a valid coarse/fine pair with different spatial resolutions.
- [ ] Accept supported temporal alignment and reject an undeclared cadence mismatch.
- [ ] Correctly handle or explicitly reject nonzero first lead, reordered/extra leads, and nonhourly spacing; labels and scored durations remain accurate.
- [ ] Reject a report with the wrong run, input digest, member set, radar identity, scoring grid, or inconsistent summary counts.
- [ ] Reject a score for different forecast bytes even when its event ID and initialization match.
- [ ] A complete synthetic forecast-to-report-to-manifest integration test passes without GPU/network dependencies.
- [ ] A source-content change invalidates the stored identity even when filenames remain unchanged.
- [ ] Existing v1 manifests remain readable; new manifests expose accurate data kind and separate initialization/publication times.

### Delivery and recovery

Land the contract and tests, then source writers/validators, then report attachment and web support. Preserve original legacy artifacts. If a new release fails validation, retain the last validated release and report the failure; do not fall back to unvalidated encoding.

## 2. Make GPU launches and publishing atomic

### Problem and evidence

The daily claim uses a read followed by an unconditional write. Two mocked concurrent handlers both launched pods; only the second pod ID survived in the marker. Deployment permits operation without reserved concurrency. The reaper limits eventual exposure, but it does not prevent duplicate spend or termination of the wrong attempt.

Catalogue and verification-index updates also use read/merge/write. A test showed yesterday's scored update being overwritten by today's stale snapshot while both publishers returned success. A separate test showed an index-read timeout being treated as an empty history and overwriting existing records.

Existing implementation touchpoints:

- [Launcher](C:/Users/User/Desktop/GitHub/latent-sky/infra/daily/lambda_launch.py), [publisher](C:/Users/User/Desktop/GitHub/latent-sky/infra/daily/lambda_publish.py), and [checker/reaper](C:/Users/User/Desktop/GitHub/latent-sky/infra/daily/lambda_check.py).
- [Daily deployment](C:/Users/User/Desktop/GitHub/latent-sky/infra/daily/deploy-daily.sh), [pod script](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/pod_daily.sh), and [manual site deployment](C:/Users/User/Desktop/GitHub/latent-sky/infra/web/deploy-site.sh).
- The three existing daily Lambda test modules and [daily operations documentation](C:/Users/User/Desktop/GitHub/latent-sky/infra/daily/README.md).

### Implementation

1. **Define ownership separately from scientific identity.** A run ID identifies forecast inputs; an attempt ID identifies one execution; a release ID identifies one complete published output. Persist these IDs in claims, pod metadata/names, uploaded object prefixes, completion records, and logs. Record the pod ID once known. Track claimed, launching, running, completed, failed, and uncertain states with version-checked transitions.

2. **Acquire the daily claim atomically.** Prefer the existing S3 infrastructure: create a previously absent claim with `If-None-Match: *`, and update an existing record against its ETag using `If-Match`. A lost precondition requires rereading ownership, not an unconditional retry. Package compatible SDK/CLI versions and retain reserved concurrency as additional protection. AWS documents both conditional-write mechanisms. [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)

3. **Handle uncertain pod creation.** A timeout may follow successful pod creation. Keep the attempt in an uncertain state and reconcile against provider pods using its unique identity before considering a replacement. Do not automatically expire/delete the claim and launch again. Use provider idempotency only if its supported semantics are verified during implementation. Without such support, conservatively block automatic replacement after ambiguous creation; an atomic S3 claim alone cannot guarantee exactly-once execution across two services.

4. **Make retries and forced reruns safe.** An explicit rerun creates a new attempt with a recorded predecessor. It must not bypass active ownership or overwrite an unresolved attempt. Completion and termination must target the exact attempt/pod, not whichever pod ID is currently stored under the date. Have the reaper reconcile all attempts, including orphaned pods, and retain the existing runtime deadline.

5. **Publish immutable release contents.** Upload each release's frames, LUTs, manifest, report, and score data under a unique revision/content-addressed prefix. Validate the complete inventory, hashes, and provenance before promotion. Weather assets must never change beneath a URL served with immutable caching. Preserve the stable catalogue event ID while pointing it to the new release manifest.

6. **Make one per-run release record authoritative.** Promote a complete release with a version-checked update to the run's current-release record. Prevent an older unscored completion from replacing a newer scored release of that run. Allow a corrected report only as an explicit new revision carrying its own evidence and supersession record. Record durable completion only after the required publication steps succeed.

7. **Protect all shared indexes.** Update catalogue and verification index with ETag-checked read/merge/write and bounded retries. On conflict, read the current document and reapply the intended change. Only a definite missing-object response may initialize an empty index; timeouts, permission failures, and malformed JSON must fail without replacing it. Update manual deployment to use the same protocol.

8. **Recover partial multi-object publication.** S3 conditional writes protect individual objects; they do not make the catalogue, report, and index one transaction. Generate these views from authoritative run/release records and add an idempotent reconciliation step for interrupted publication. Retention must not remove releases still referenced by any published view or an in-progress update. Keep old releases through a defined cache/grace period before cleanup.

### Regression tests and acceptance criteria

- [ ] Two simultaneous daily claims result in exactly one provider create call.
- [ ] A create timeout after simulated provider success does not trigger another create call.
- [ ] Replayed completion events are harmless, and termination always targets their own recorded attempt.
- [ ] A forced rerun cannot overwrite or bypass an unresolved active attempt.
- [ ] Concurrent catalogue/index writers preserve both updates, including a transition to scored.
- [ ] A late unscored completion cannot regress a scored release.
- [ ] An index timeout, access denial, or malformed response causes no destructive PUT; a confirmed missing index can be initialized.
- [ ] Interrupted publication recovers through replay/reconciliation without advertising missing assets or reports.
- [ ] Old and new manifests resolve to internally consistent imagery through a simulated cache.
- [ ] An isolated staging smoke test verifies actual conditional-write behaviour before production rollout. Unit race tests remain deterministic and cloud-free.

### Delivery and recovery

Deploy atomic ownership first, then attempt-aware completion/reaping, then immutable releases and conditional indexes. Inventory outstanding pods/claims before changing their format. Keep old readers during migration. Recovery selects a previous complete release; it must not restore unconditional shared writes or erase claim history. Reconcile uncertain attempts before changing the launcher back to an older version.

## 3. Make viewer readiness and errors truthful

### Problem and evidence

When every weather-frame request returned HTTP 404, the app reported `ready=true`, retained a weather legend, and showed no visible error. A delayed wind legend request also overwrote the colour scale after the user switched to water vapour. The displayed label remained water vapour.

Existing implementation touchpoints:

- [Bitmap provider/cache](C:/Users/User/Desktop/GitHub/latent-sky/web/src/globe/bitmapProvider.ts), [globe lifecycle](C:/Users/User/Desktop/GitHub/latent-sky/web/src/globe/index.ts), [layers](C:/Users/User/Desktop/GitHub/latent-sky/web/src/globe/layers.ts), and [renderer](C:/Users/User/Desktop/GitHub/latent-sky/web/src/globe/render.ts).
- [App](C:/Users/User/Desktop/GitHub/latent-sky/web/src/ui/App.svelte), [legend](C:/Users/User/Desktop/GitHub/latent-sky/web/src/ui/Legend.svelte), [store](C:/Users/User/Desktop/GitHub/latent-sky/web/src/state/store.svelte.ts), and [browser smoke tests](C:/Users/User/Desktop/GitHub/latent-sky/web/tests/smoke.spec.mjs).

### Implementation

1. **Define explicit load state.** Expose loading, ready, degraded, and error states with the active event, variable, frame, and load-generation ID. Ready means the required active weather frame(s) and legend are decoded and presented, not merely that Cesium's tile queue is empty. A basemap-only globe cannot satisfy weather readiness.

2. **Track required assets separately from prefetch.** At rest, require the selected frame and currently visible comparison layers. During interpolation, require both contributing frames. Fetch/decode only a bounded look-ahead window; distant prefetch failure should not make a successfully loaded active frame unusable. Remove the hardcoded initial-wind selection so loading follows the manifest's declared default variable.

3. **Propagate errors into the UI.** Add bounded request/decode deadlines and cancellation on event/variable changes. Propagate active-frame HTTP, decode, and render errors to the facade/store. Show an accessible status message and Retry action. If retaining the previous successfully rendered frame, keep its actual timestamp visible and clearly mark the requested frame unavailable. Disable the normal legend/comparison claim when no corresponding data is shown.

4. **Make view changes transactional.** Stage the incoming event/variable assets before committing its displayed labels and frame state. Retain the previous valid view when staging fails, or show an explicit unavailable view if retention is impractical. Ensure a failed switch can be retried and that successful retry clears the error. Resolve or reject outstanding readiness promises on timeout/disposal.

5. **Prevent stale asynchronous writes.** Give each legend load a generation token or cancellation guard. Render the decoded LUT and its label/bounds together only if the event/variable still matches. Apply the same guard to imagery callbacks, errors, and readiness resolution. An old event's completion must not change the new event's state.

6. **Manage rendering resources.** Cancel obsolete fetches, close bitmaps after their consumers release them, and cap cache usage by decoded bytes as well as count. Handle render/context loss with a visible recoverable state. Preserve existing reduced-motion behaviour and session disposal guarantees.

### Regression tests and acceptance criteria

- [ ] All weather images returning 404 results in a visible error and `ready=false`.
- [ ] A corrupt image and a request that never completes produce bounded, actionable failure.
- [ ] One failed future prefetch does not erase an otherwise valid active frame.
- [ ] Playback cannot silently advance the displayed timestamp over missing weather.
- [ ] Delay the old LUT, change variables, then release the old response: the current legend remains correct.
- [ ] Rapid event/variable switches and a failed-then-successful retry cannot resurrect stale state.
- [ ] Successful comparison views wait for both visible sides and their correct time alignment.
- [ ] Existing seven smoke scenarios, keyboard controls, reduced motion, and the transfer budget continue to pass.

### Delivery and recovery

Land the stale-legend fix and reproductions first, then asset-state tracking, readiness, and retry UX. Keep the facade as the boundary between Svelte and Cesium. Release behind a temporary configuration flag only if it helps staged validation; production must never silently revert to treating missing weather as ready.

## 4. Enforce reproducibility and adversarial checks in CI

### Problem and evidence

The determinism workflow treats every source-download failure as an optional missing release. If downloading succeeds, it invokes `make encode`, which does not exist. Its final `git diff` also does not prove equality of a clean, complete output inventory. Current browser CI primarily exercises synthetic happy paths.

Existing implementation touchpoints:

- [Determinism workflow](C:/Users/User/Desktop/GitHub/latent-sky/.github/workflows/encode-determinism.yml), [web workflow](C:/Users/User/Desktop/GitHub/latent-sky/.github/workflows/web.yml), [licence workflow](C:/Users/User/Desktop/GitHub/latent-sky/.github/workflows/licences.yml), and [Makefile](C:/Users/User/Desktop/GitHub/latent-sky/Makefile).
- [Pipeline environment](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/pyproject.toml), [web package scripts](C:/Users/User/Desktop/GitHub/latent-sky/web/package.json), and [transfer-budget checker](C:/Users/User/Desktop/GitHub/latent-sky/web/tools/transfer-budget.mjs).
- Existing pipeline, Lambda, and browser tests, including forecast, observation, and probability verification scripts.

### Implementation

1. **Establish an artifact registry.** Pin an explicit release/tag, archive name, checksum, licence/source provenance, encoder configuration, and expected output inventory for each required representative fixture. Use compact synthetic fixtures on every pull request and an approved real-run fixture for production re-encoding. Never use the restricted development sample as a public CI artifact. If the real artifact is not yet available, track that blocker explicitly; do not call the release gate complete.

2. **Provide one real encoding entry point.** Implement and document the missing target or replace the workflow command with an existing production CLI. Make source/config/output arguments explicit and portable. Produce into a new staging directory. Fail on a missing input, archive/checksum mismatch, unavailable required download, or incomplete output.

3. **Define determinism boundaries.** Pin the encoder's Python/image-library environment and record the toolchain version. Compare two clean encodes and the approved expected artifact using a complete sorted file inventory and hashes. Fail on added, removed, or changed files. Keep generation/publication timestamps outside byte-identical payloads or supply fixed recorded metadata; document every permitted volatile field rather than broadly ignoring manifest differences.

4. **Run checks on pull requests and releases.** Add `pull_request` triggers and path coverage for schemas, shared helpers, infrastructure, configs, dependency pins, and the Makefile. Keep an always-reporting aggregate status so path-filtered jobs do not leave required checks unresolved. Configure protected-branch required checks as part of implementation; a workflow file alone does not enforce merging policy. Do not expose deployment credentials or run deployment scripts in untrusted PR jobs.

5. **Add adversarial test jobs.** Run the Lambda tests and deterministic race/failure cases from workstream 2. Add provenance-negative and score-attachment tests from workstream 1. Add image failure, timeout, delayed-LUT, and failed-switch cases from workstream 3. Adapt existing real-event scripts to deterministic local fixtures where practical.

6. **Exercise the actual release layout.** Add a production-build browser smoke test that serves the staged site, including its manifest, data, and Cesium assets. The existing development-server test remains useful, but it cannot prove production staging works. Assert expected content types, absence of unintended external requests, correct asset paths, and readable reports. Make the payload gate fail on missing referenced files rather than undercounting them.

7. **Separate fast checks from expensive checks.** Keep synthetic tests GPU-free and network-independent after dependency installation. Give required archived-data encoding jobs explicit timeouts and logs. Run expensive model benchmarks separately with a fixed budget; require their published result for scientific releases. Cache verified inputs, never skip a required assertion because a cache or network fetch fails.

### Regression tests and acceptance criteria

- [ ] A missing/unreachable required artifact fails with a clear reason.
- [ ] The documented production encoding command succeeds in a clean checkout.
- [ ] An intentionally added, removed, or modified output file fails the inventory comparison.
- [ ] All adversarial reproductions fail on the old implementation and pass on their fixes.
- [ ] Infrastructure and provenance changes trigger the appropriate jobs on pull requests.
- [ ] The production browser smoke runs against a staged build and catches a missing frame/Cesium asset.
- [ ] Required branch checks are configured, and no required job silently turns absence into success.
- [ ] A CI run publishes enough logs and artifact digests to reproduce a failure locally.

### Delivery and recovery

Start with PR triggers and the documented encoding command. Add the input registry, environment pinning, and inventory comparison next. Wire in each regression suite as its implementation lands. Update golden outputs only with an explained, reviewed change and a successful complete re-encode; a failing gate is not resolved by suppressing the assertion.

## 5. Establish credible scientific baselines and measured uncertainty

### Problem and evidence

The generated description says reflectivity has no coarse counterpart. NOAA's GFS 0.25-degree product inventory includes `REFC`, composite reflectivity. This does not mean GFS resolves individual thunderstorms like a convection-allowing model, but it makes the blanket absence claim incorrect. [NOAA GFS inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2.0p25.f003.shtml)

The ensemble changes sampler seed while retaining the same initial conditions. Its agreement map is useful, but agreement alone does not establish calibrated probabilities or encompass all forecast uncertainty. Existing FSS verification provides spatial evidence; it does not by itself validate those confidence claims.

Existing implementation touchpoints:

- [Generated descriptions and probability encoding](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/src/latentsky/encode_stormcast.py), [scoring functions](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/src/latentsky/verify.py), and [verification CLI](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/tools/verify_fss.py).
- [Report generator](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/tools/fss_report.py), [report template](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/tools/fss_report.html), [verification index](C:/Users/User/Desktop/GitHub/latent-sky/pipeline/tools/verification_index.py), and [caveat UI](C:/Users/User/Desktop/GitHub/latent-sky/web/src/ui/Caveat.svelte).
- [Asset licence manifest](C:/Users/User/Desktop/GitHub/latent-sky/licences/MANIFEST.yaml), [README](C:/Users/User/Desktop/GitHub/latent-sky/README.md), and the event configurations.

### Implementation

1. **Correct unsupported wording immediately.** Explain that this project's coarse layer currently omits reflectivity, and distinguish output grid spacing from resolved physical detail. Describe the current probability layer as the fraction of sampled members exceeding the threshold; state that spread here reflects sampler variation with fixed initial conditions. Replace categorical confidence/guessing wording. Publish copy changes through a new release revision rather than overwriting immutable data.

2. **Write the evaluation protocol before selecting results.** Create a versioned benchmark specification listing cases, domains, cycles, lead times, observation products, thresholds, neighbourhood sizes, spin-up exclusion, coverage masks, and missing-data rules. Start with a manageable pilot containing quiet days, widespread convection, localized storms, and weak-skill cases. Freeze an evaluation set separate from cases used to tune thresholds or select models. A pilot is exploratory evidence, not a guarantee of broad skill.

3. **Inventory data before spending.** Check archived HRRR/GFS forecasts and MRMS observations for the chosen periods, their issue/availability times, model versions, variable definitions, and licences. Reuse valid existing forecast outputs. Record expected downloads, storage, GPU runtime, and a spending ceiling before generating new cases. If an archive or checkpoint history is unavailable, disclose that limitation instead of substituting newer information without a record.

4. **Implement meaningful comparators.** Add available HRRR forecast and GFS composite-reflectivity baselines, plus a simple radar-persistence baseline based only on information available at issue time. Define which comparisons test operational value and which compare model cycles. Use consistent valid times, masks, thresholds, and a documented regridding method. Preserve the native-grid distinction and explain differences in reflectivity diagnostics; do not imply all products measure identical vertical quantities merely because their units match.

5. **Extend measurements beyond one headline.** Report FSS by lead time, threshold, and neighbourhood alongside forecast/baseline differences. For probabilistic output, implement Brier scores and reliability diagrams with sample counts, sharpness, and a declared reference forecast. Treat missing radar as unavailable, not no precipitation. Test no-event cases and undefined metrics explicitly. Aggregate uncertainty over independent cases/blocks rather than pretending neighbouring pixels are independent samples.

6. **Prevent retrospective leakage and selective reporting.** Separate hindcasts from forecasts actually published before the event. Record initialization, input availability, generation, and publication times through workstream 1. Document possible model-training overlap when known. Keep every planned case in the benchmark index, including failures, unavailable observations, pending scores, and negative results. Do not redefine the test set after seeing outcomes.

7. **Publish reproducible, bounded conclusions.** Emit machine-readable results with provenance and regenerate reports/index pages from them. Show the comparator, sample count, valid coverage, lead window, and uncertainty next to aggregate results. Describe calibration only to the extent supported by the measured sample. Publish improved results and weak results under the same policy.

8. **Align project presentation with the evidence.** Update the README's pre-release/quota statements to the verified current state. Separate initialized-at from issued/published-at in the viewer, and label historical replays accurately. Replace the unconditional promise that AI detail arrives with the first forecast when the catalogue already contains AI events. Provide a direct route to an existing verified case and its report.

### Regression tests and acceptance criteria

- [ ] Corrected descriptions appear in newly encoded manifests and rendered UI/report fixtures.
- [ ] A frozen benchmark specification and complete input inventory exist before evaluating additional cases.
- [ ] Baselines use only information allowed by the declared issue-time protocol.
- [ ] Hand-calculated synthetic examples validate FSS, Brier, reliability binning, missing-data masks, and no-event behaviour.
- [ ] At least one complete pilot comparison can be regenerated from recorded source artifacts and checksums.
- [ ] Published results include all planned cases and disclose missing observations and model-training limitations where applicable.
- [ ] Reports show comparator results and uncertainty/sample counts; seed agreement is not presented as established calibration without supporting measurements.
- [ ] Hindcasts, reanalysis, and genuinely pre-event forecasts are visibly distinguishable.

### Delivery and recovery

Ship the copy/provenance presentation fixes first. Land the benchmark specification and comparator adapters next, then metrics and report changes. Complete the pilot before expanding GPU runs. If a metric or source mapping is found incorrect, mark the affected result revision superseded, publish a correction, and preserve its history. Do not silently remove a poor result.

## Final verification and completion record

The original review passed the frontend type check/build, seven browser smoke scenarios, 52 targeted pipeline tests, 35 infrastructure tests, and the transfer gate at 10.61 MB against a 12 MB ceiling. The full pipeline suite was stopped before completion. Cloud races used mocks; live GPU/cloud operations were not exercised. These are review-time observations, not proof that this implementation plan has been completed.

Use this final checklist when the workstreams are implemented:

- [ ] Record the implementing commit/PR and test evidence for every workstream.
- [ ] Run the complete pipeline suite and investigate the earlier long-running portion; do not equate a targeted pass with full coverage.
- [ ] Run all Lambda, browser, schema, licence, deterministic-encode, inventory, and payload checks.
- [ ] Confirm only approved source data and complete validated releases are publishable.
- [ ] Rehearse the ownership and publication changes in an isolated staging environment, including interruption, retry, and cache behaviour.
- [ ] Verify an ordinary forecast, an unscored run, a scored run, a missing-image case, and a legacy event through the production build.
- [ ] Demonstrate restoration of a previous complete release without changing immutable asset contents or losing attempt history.
- [ ] Record benchmark coverage, unresolved limitations, and any deferred items with an owner and explicit completion condition.

The implementation is complete when the known reproductions are prevented, the regression gates enforce those guarantees, and the public claims can be traced to the correct run and evidence.
