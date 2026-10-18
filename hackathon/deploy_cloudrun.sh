#!/usr/bin/env bash
set -euo pipefail
: "${DEMO_API_IMAGE:?Supply the tested immutable image digest}"
project="${DEMO_PROJECT:-aisle-mint-prd}"
region="${DEMO_REGION:-us-central1}"
service="aibuildercup-api"
instance="${DEMO_SQL_INSTANCE:-aisle-mint-prd:us-central1:aisle-mint-db}"
identity="aibuildercup-demo@${project}.iam.gserviceaccount.com"
[[ "$DEMO_API_IMAGE" == *@sha256:* ]] || { echo 'An immutable image digest is required.' >&2; exit 1; }
gcloud run deploy "$service" --project="$project" --region="$region" \
 --image="$DEMO_API_IMAGE" --service-account="$identity" --port=8000 \
 --execution-environment=gen2 --cpu=1 --memory=2Gi --min=1 --max=1 \
 --concurrency=4 --timeout=300 --no-cpu-throttling \
 --network=default --subnet=default --vpc-egress=private-ranges-only \
 --add-cloudsql-instances="$instance" \
 --set-env-vars="^|^AISLEMINT_HACKATHON=1|POSTGRES_DATABASE=aislemint_hackathon|POSTGRES_USER=aibuildercup_app|POSTGRES_HOST=/cloudsql/$instance|POSTGRES_PORT=5432|POSTGRES_SSLMODE=disable|POSTGRES_POOL_SIZE=8|PUBLIC_ORIGIN=https://aibuildercup.aislemint.com|PUBLIC_ORIGINS=https://aibuildercup.aislemint.com|TRUSTED_HOSTS=aibuildercup.aislemint.com,localhost,127.0.0.1,*.run.app|RETAIL_TRAINING_WORKER=inline|CONNECTED_WORKER=inline|DATA_PLATFORM_WORKER=inline|LEGACY_TRAINING_WORKER=external|OMP_NUM_THREADS=1|OPENBLAS_NUM_THREADS=1" \
 --set-secrets=DEMO_RUNTIME_SECRET=aislemint-hackathon-runtime:1 \
 --labels=application=aislemint,environment=hackathon --allow-unauthenticated --quiet
