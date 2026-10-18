# Isolated hackathon deployment

## Current activation status

**Prepared, not yet activated.** The standalone source, synthetic seed, anonymous sandbox, model handoff, local checks and separate container images are prepared. A dedicated empty Cloud SQL database and service identity have been created. Activation is waiting for the owner's approval of the specific runtime-secret transfer and scoped permissions; the deployed product has not been switched to this copy.

Do not submit the hostname as a working demo until the public HTTPS and fresh-browser checks below are recorded as passed.

## Resource boundary

| Resource | Hackathon copy |
|---|---|
| Repository | `Akash-M1/AisleMint-AIBuilderCup` |
| Public hostname | `aibuildercup.aislemint.com` |
| DNS | A record to `172.232.85.211`; owner reports saved |
| API | Dedicated Cloud Run service `aibuildercup-api`, planned in `us-central1` |
| API identity | `aibuildercup-demo@aisle-mint-prd.iam.gserviceaccount.com` |
| PostgreSQL database | `aislemint_hackathon` on the existing Cloud SQL instance |
| Database role | New `aibuildercup_app`, no production customer-table privileges |
| Runtime secret | Dedicated `aislemint-hackathon-runtime` containing only demo DB password and configured Gemini key |
| Frontend | Separately named image/container on the existing server |
| Visitor records | Separate synthetic retailer/workspace/ledger per browser session |
| Model artifacts | Immutable fits from the common synthetic template; no customer models copied |

The existing server, Cloud project and Cloud SQL **instance infrastructure** are shared. The application service, database, database credential, identity, source and operational records are separate. This is data/application isolation, not a claim of physically separate hardware. Model-call quotas also protect usage of the configured Gemini key, whose provider quota may be shared with the original key owner.

## Deployment sequence

1. Provision only the named database and an unprivileged login. Revoke public access to the demo database and grant that login schema creation/use there. Verify it cannot select production customer tables using privilege metadata only.
2. Seed with `python -m hackathon.seed /protected/demo.env`. The command requires the exact demo database name. The seed generates history, trains/evaluates models, creates the ledger and publishes synthetic connected data. It never reads customer tables.
3. Use a dedicated Secret Manager secret. Grant the new service identity `roles/secretmanager.secretAccessor` on that secret only, plus the Cloud SQL connectivity role needed by the Cloud SQL integration. It must receive no production secret grants.
4. Build the API and frontend from this repository. `hackathon/Dockerfile.api`, `hackathon/Dockerfile.frontend` and the optional Cloud Build configuration are included. If Cloud Build submission is unavailable to the operator account, build the same Dockerfiles on the server under `/opt/aibuildercup/src`; do not replace existing product images.
5. Push the tested API image to Artifact Registry and deploy its immutable digest with `hackathon/deploy_cloudrun.sh`. That script names only the new service. It sets one warm instance, one maximum instance, four-request concurrency and a two-GiB memory limit. Inline workers process only this database's jobs.
6. Start the separate frontend using `hackathon/compose.yaml`, setting `DEMO_FRONTEND_IMAGE` to its tested image.
7. Back up and validate the existing HTTPS router configuration. Add only the `aibuildercup.aislemint.com` host: route `/api/*` to the new Cloud Run URL and all other paths to `aibuildercup-web:3000`. Preserve all existing hosts. Validate before restarting/reloading the router and retain rollback copies.
8. Verify certificate issuance, guest entry in a fresh browser, independent workspaces, page/model data, model labels, confirmation→POS pricing, demo receipt and a reset. Probe the regular website, platform health and tracker afterward.

The main product release process can replace its shared router file. Preserve the additive demo host block during future router releases while judging is active. No recurring automation is installed by this repository.

## Access and limits

Visitors never enter credentials. `/api/demo/session` creates a short-lived anonymous synthetic identity; normal retailer permissions enforce ownership. `/api/demo/pos-session` unlocks only that visitor's synthetic register. The reset endpoint creates a new guest namespace and revokes the previous guest session.

Mode and parsed database connection-name checks fail closed. AI-key updates, membership/key management, provisioning/payment callbacks and external connector bindings are blocked. The app retains no SMTP, WhatsApp or payment-gateway credentials.

Application quotas are stored in the demo database and survive API restarts. They apply to guest creation, mutation volume, training and actual model attempts. A reset does not reset global quotas. Review cost and storage retention after judging; automatic deletion of old visitor workspaces is not enabled.

## Recovery

- Roll the new Cloud Run service back to its previous tested image digest; do not change the production API service.
- Revert only the new router host block if routing fails, retaining the backup and existing host routes.
- Stop only the `aibuildercup` Compose project to disable the demo frontend.
- Do not drop the demo database as part of an ordinary restart. Re-seeding a completed template is idempotent and preserves visitor records. To regenerate a new baseline, first back up or deliberately recreate only the named demo database.
- Keep private runtime files under operator-controlled, ignored storage. No populated environment file, provider key, SQL password or service-account JSON belongs in Git.
