# GPU day runbook

## ⚠ Account boundary — read before anything else (16 Aug 2026)

The AWS credentials configured as the DEFAULT profile on the dev machine belong to
**account 093047596153 — Kira's company. Nothing from Latent Sky may touch it.**
Every script here sources `account_guard.sh`, which hard-refuses that account and
requires `LATENTSKY_AWS_ACCOUNT=<personal account id>` to be set explicitly.

### Setting up the new personal account (one-time, before any GPU work)

1. **Sign up on the PAID account plan, not Free.** This is decided at signup and is
   load-bearing: CloudFront flat-rate plans (the whole §9.3 bill-cap design) are
   unavailable to Free-plan accounts, and Free-plan accounts self-close after 6 months.
   You are trading signup credits for a permanent spend cap. Take the trade.
2. **File the GPU quota request immediately** — Service Quotas → EC2 →
   "Running On-Demand G and VT instances" (`L-DB2E81BA`) → 8 vCPUs, us-east-1.
   A new account's default is **0** and approval is human-reviewed (days). This is
   the clock that starts everything else; file it the same hour the account exists.
   Leave the P-instance quota at 0 — that keeps the $55/hr tier unlaunchable.
3. Root MFA on; a $50 budget with 50/80/100% alerts plus an action-enabled budget
   that stops EC2 (first two action budgets are free).
4. Configure the CLI as a **named profile, never default**:
   `aws configure --profile latentsky`, then for every session here:
   `export AWS_PROFILE=latentsky LATENTSKY_AWS_ACCOUNT=<new account id>`.
5. Verify the guard passes: `bash infra/gpu/account_guard.sh` prints
   `account guard OK: <id> (personal)`.

The rented-GPU session is the only unrecoverable spend in the project, so the day is
scripted to be boring. Total inference is trivial (the shipped CorrDiff example runs in
~21 s); the entire risk is environment friction and forgetting to turn things off —
both of which this kit removes. Worst-case cost is bounded by the deadman at ~$9.

## Before quota approval (all free, all done from the laptop)

- [ ] **Quota**: `L-DB2E81BA` ("Running On-Demand G and VT instances") = 8 vCPUs,
      us-east-1, requested. **Leave the P-instance quota at 0** — that keeps the
      $55/hr tier physically unlaunchable.
- [ ] **Plan eligibility**: CloudFront → Manage Plan shows the flat-rate subscribe
      control. If it does not, stop and revisit Architecture.md §9.3 — the cost design
      changes.
- [ ] **Image built**: `docker build -t latentsky-forecast:0.17.0 pipeline/`
      (needs ~25 GB free disk for the NVIDIA base — check first). Then the models
      variant: `docker build --build-arg BAKE_MODELS=1 -t latentsky-forecast:0.17.0-models pipeline/`.
- [ ] **Local smoke on the RTX 3060** (optional but valuable, $0) — run from **PowerShell**
      (Git Bash mangles `/opt/...` paths into `C:/Program Files/Git/...`):
      `docker run --rm --gpus all -v "C:\Users\User\.cache\latentsky-models:/cache/earth2studio" latentsky-forecast:0.17.0 --config /opt/latentsky/configs/event_doksuri_2023.yaml --dry-run`
      Status 2 Aug 2026: install ✓, 7.9 GB packages cached ✓, checkpoints load on CPU ✓,
      GPU step blocked by the host's CUDA 12.6-era driver — update the GeForce driver
      and rerun for the full pass. Rented instances ship current drivers; this blocker
      is local-only.
- [ ] **ECR**: repository created; image pushed.
- [ ] **S3**: bucket created; `BUCKET=<bucket> ./prefetch-models.sh` run once.
- [ ] **VPC**: public subnet with an **S3 Gateway endpoint** (Endpoints → Gateway →
      com.amazonaws.us-east-1.s3). Never a NAT Gateway (§9.4 — $9 of pure overhead per run).
- [ ] **IAM**: instance profile granting S3 read/write on the bucket + ECR pull.
- [ ] **Budgets**: $50 budget with 50/80/100% alerts; a second action-enabled budget
      that stops EC2. Free. Detection, not prevention — the deadman is the prevention.

## The day

1. **Probe 5** (~$0.01, 5 min): `./rehearse-deadman.sh` — must print PASSED.
   If it prints anything else, the day is over before it starts, deliberately.
2. **Probe 1** (~$1, 30 min): `BUCKET=… IMAGE=… ./launch-gpu.sh --config event_doksuri_2023.yaml`
   — NVIDIA's own tested date. Watch `runs/<id>/run.log` land in S3. If this fails,
   the failure is on NVIDIA's example path, which is a far easier debugging problem.
3. **The hero** (~$5): `./launch-gpu.sh --config event_gaemi_2024.yaml`.
4. **Fetch + encode (laptop, free)**: `aws s3 sync s3://$BUCKET/runs/<id>/ data/zarr/`,
   then adapt `latentsky.encode_dev` to the run Zarr (M4 task), re-run the budget gate,
   point the web app at the new manifest.
5. **Same-day audit** (§9.4): no running instances, no unattached volumes, no
   Elastic IPs, no NAT Gateways. Two minutes in the console.

## If things go wrong

- **OOM on g6e (48 GB)**: relaunch with `INSTANCE_TYPE=g7e.2xlarge` (96 GB, ~$3.36/hr)
  — same G-family quota, no new approval needed. Budget an hour for driver friction.
- **Gaemi output looks scientifically wrong**: retreat to `event_koinu_2023.yaml`,
  NVIDIA's own worked-example date.
- **Anything hangs**: do nothing. The deadman fires at 4 h and the worst case is ~$9.
  That is the design working, not failing.
