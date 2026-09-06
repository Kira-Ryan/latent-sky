# Verification protocol

Version 2, in force from 7 September 2026. Version 1 ran from 4 to 6 September 2026 and is described at the end. Every scored run states which version produced its figures, in the `rule` field of its results file and its manifest.

This page states the rules by which Latent Sky scores its own forecasts against radar. It is written before the results, not after them: the daily run is scored and listed whatever the outcome, and a change to these rules is a new version applied from a stated date, never a quiet edit.

## What is scored

The StormCast composite reflectivity forecast over the central United States, initialised at 12 UTC from the HRRR analysis and conditioned on the 12 UTC GFS forecast, 3 km native grid, hourly to 18 hours. The verification field is NOAA's MRMS composite reflectivity, the merged radar mosaic, at the same instants.

Both fields are placed on the display grid, the grid the site renders, so that what is scored is what is shown: 884 by 661 cells over the domain rectangle, 2.39 km per cell. The forecast is sampled from its native grid by nearest cell; MRMS is resampled from its 0.01 degree grid the same way.

## Matching in time

Each forecast hour is matched to the MRMS file nearest to its valid time. A match is refused if the nearest file is more than 300 seconds away, and the worst offset used is recorded in the results. A run whose forecast times do not equal the observation times is not scored.

## The score

The Fractions Skill Score of Roberts and Lean (2008). At each threshold, both fields become binary exceedance masks. Each mask is replaced by the fraction of exceeding cells in a square neighbourhood around every cell. The score is one minus the mean squared difference of the two fraction fields, divided by the mean of the squared fractions of each. It is one for a perfect forecast, zero for one that places nothing where anything was, and rises with neighbourhood size because displacement is forgiven at scale.

Thresholds: 20, 30 and 40 dBZ. The headline is at 40 dBZ, the threshold at which reflectivity means a strong storm rather than rain.

Neighbourhoods: 1, 5, 11, 21 and 41 cells, which is 2.4, 12, 26, 50 and 98 km on the display grid. 98 km is the largest scale tested, and every statement of "no useful skill" says so.

Radar coverage: cells with no radar coverage are masked out of both fields before the fractions are computed. Cells outside the mask are zero in both fields before filtering, so the edge of the mask neither creates nor absorbs neighbourhood mass.

## The useful line

Roberts and Lean's criterion: a forecast is useful at a scale when its FSS exceeds 0.5 plus half the observed base rate, the fraction of covered cells that exceed the threshold. The base rate is computed per hour from the radar, so the line moves with how much weather there was.

## Which hours count

The first two forecast hours are never scored. A convection-allowing model handed an analysis scores trivially well against it before it has done any work, and counting those hours would flatter the run.

Of the remaining hours, an hour is scorable when both of these hold:

- its FSS is defined, meaning some cell exceeded the threshold in the forecast or the radar. A correct forecast of no strong storms is undefined, not wrong, and is never published as a failure;
- radar covers at least half of the grid cells in that hour.

Hours that fail either test are reported as undefined and excluded from every aggregate.

## The headline

The one figure the globe states without the reader opening the report.

Over the scorable hours, the useful scale is the smallest neighbourhood at which the mean FSS reaches the mean useful line, both means taken over the same hours. The number of hours individually above the line at that scale is reported beside it, never instead of it.

Three verdicts are possible, and all three are published:

- **useful**: the mean reaches the line at some tested scale. The site says at which scale, and says so if that scale is the largest tested.
- **below-line**: the mean reaches the line at no tested scale. The site says so, with the mean and the line at the largest scale and the count of hours above it there.
- **no-echo**: no hour was scorable. The site says there was nothing to score.

Why the mean and not any single hour: version 1 named the smallest neighbourhood at which at least one hour cleared the line, so a single chance crossing at the coarsest scale was headlined as useful skill. That happened on 4 and 5 September 2026. Why the mean and not the median: FSS is conventionally averaged over cases, and the hour count already shows the shape of the distribution.

## Ensembles

When a run has ensemble members, differing only in the diffusion sampler's seed, the probability of exceedance across members is scored with the probabilistic FSS on the same grid, thresholds and neighbourhoods, and the single deterministic run is scored alongside for contrast. Member agreement is sampler spread with the initial condition held fixed. It is not a calibrated probability and it cannot see an error the members share; no reliability claim is made until reliability has been measured.

## What is not claimed

No comparison against another forecast system is made yet, so a score says how the forecast placed strong storms, not whether it placed them better than the operational model would have. Radar persistence and the same-cycle HRRR forecast are the intended comparators and will appear here, under a new version, when they are scored.

The daily run is initialised at 12 UTC and reaches the site around 16:15 UTC. Forecast hours two to four have therefore already happened by the time the run is public. They are scored like any other hour; a reader weighing the forecast as a prediction should discount them.

## Timing and publication

The run for a day is scored the next day, once the radar for its whole window exists, and republished with the observed reflectivity beside the forecast and a link to its report. An unscored run says "not yet scored" until it is scored. If scoring fails, the run stays unscored and the failure is reported to the operator; a run is never dropped from the record because it scored badly.

## Version history

- **Version 2, from 7 September 2026.** The headline is the mean FSS over scorable hours against the mean useful line. Hours with undefined FSS or radar coverage under half the grid are excluded and reported. Results already published under version 1 were rescored under version 2 on 6 September 2026; their reports and manifests carry the version 2 figures and this note.
- **Version 1, 4 to 6 September 2026.** The headline was the smallest neighbourhood at which at least one post-spin-up hour cleared the useful line, with the count of such hours. It counted undefined hours as scored and had no coverage floor.
