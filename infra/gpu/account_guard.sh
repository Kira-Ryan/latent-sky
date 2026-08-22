#!/bin/bash
# Latent Sky AWS account guard — sourced by every script in infra/gpu/ before any
# mutating AWS call.
#
# HISTORY (16 Aug 2026): the AWS credentials configured as the DEFAULT profile on the
# dev machine belong to account 093047596153, which is Kira's COMPANY account. Nothing
# from Latent Sky may create, modify, or bill anything there. Latent Sky runs only on
# a personal account, identified explicitly via LATENTSKY_AWS_ACCOUNT.
#
# Usage in a script:   source "$(dirname "$0")/account_guard.sh"
# Callers must export LATENTSKY_AWS_ACCOUNT=<personal account id> (and ideally
# AWS_PROFILE=latentsky). There is no override for the forbidden account — if this
# guard is what stopped you, that is the guard working.

FORBIDDEN_ACCOUNT="093047596153"   # company account — hard-refused, no override

: "${LATENTSKY_AWS_ACCOUNT:?REFUSING: set LATENTSKY_AWS_ACCOUNT=<personal account id> (see infra/gpu/RUNBOOK.md — Latent Sky must never run on the default/company AWS profile)}"

ACTUAL_ACCOUNT=$(aws sts get-caller-identity --query Account --output text) || {
  echo "REFUSING: could not determine AWS identity (aws sts get-caller-identity failed)"; exit 1;
}

if [[ "$ACTUAL_ACCOUNT" == "$FORBIDDEN_ACCOUNT" ]]; then
  echo "REFUSING: current AWS credentials are the COMPANY account ($FORBIDDEN_ACCOUNT)."
  echo "Latent Sky must never touch it. Use the personal profile: export AWS_PROFILE=latentsky"
  exit 1
fi

if [[ "$ACTUAL_ACCOUNT" != "$LATENTSKY_AWS_ACCOUNT" ]]; then
  echo "REFUSING: current AWS account $ACTUAL_ACCOUNT != LATENTSKY_AWS_ACCOUNT $LATENTSKY_AWS_ACCOUNT"
  exit 1
fi

echo "account guard OK: $ACTUAL_ACCOUNT (personal)"
