# Isolated hackathon deployment

## Current activation status

**Live at https://aibuildercup.aislemint.com.** The dedicated API, frontend, trained synthetic baseline and HTTPS route are deployed. A fresh browser opens its sandbox without sign-in. Public checks passed for independent visitors, model/analytics data, a real Gemini flyer scan, promotion confirmation, a synthetic checkout receipt and reset. The existing website, platform and tracker remain healthy. See the validation record for measured scope.

## Resource boundary

| Resource | Hackathon copy |
|---|---|
| Repository | `Akash-M1/AisleMint-AIBuilderCup` |
| Public hostname | `aibuildercup.aislemint.com` |
| DNS | A record to `172.232.85.211`; resolved and HTTPS verified |
| API | Dedicated Cloud Run service `aibuildercup-api` in `us-central1` |
| API identity | `aibuildercup-demo@aisle-mint-prd.iam.gserviceaccount.com` |
| One-time seed job | `aibuildercup-seed`, same region, identity, image and database; no scheduled runs |
| PostgreSQL database | `aislemint_hackathon` on the existing Cloud SQL instance |
| Database role | New `aibuildercup_app`, no production customer-table privileges |
| Runtime secret | Dedicated `aislemint-hackathon-runtime` containing only demo DB password and configured Gemini key |
| Frontend | Separately named image/container on the existing server |
| Visitor records | Separate synthetic retailer/workspace/ledger per browser session |
| Model artifacts | Immutable fits from the common synthetic template; no customer models copied |

The existing server, Cloud project and Cloud SQL **instance infrastructure** are shared. The application service, database, database credential, identity, source and operational records are separate. This is data/application isolation, not a claim of physically separate hardware. Model-call quotas also protect usage of the configured Gemini key, whose provider quota may be shared with the original key owner.

The owner approved the dedicated runtime secret, secret-scoped accessor grant and project-scoped Cloud SQL client grant. Privilege metadata confirmed that `aibuildercup_app` is not a superuser, cannot create databases/roles, does not inherit role rights, and has no privileges on production tables or views. No production database password was copied into the demo runtime.

Deployed API revision: `aibuildercup-api-00001-g8p`. Immutable API image: `us-central1-docker.pkg.dev/aisle-mint-prd/aislemint/aibuildercup-api@sha256:db17500b65ae9fa1223cf6e00f9c728d0ffbe5b0220a78799b1d2d8ffedd04d1`. Separate frontend image: `aibuildercup-web:20261018-2`.

## Deployment sequence

1. Provision only the named database and an unprivileged login. Revoke public access to the demo database and grant that login schema creation/use there. Verify it cannot select production customer tables using privilege metadata only.
2. Seed with `python -m hackathon.seed /protected/demo.env`. The command requires the exact demo database name. The seed generates history, trains/evaluates models, creates the ledger and publishes synthetic connected data. It never reads customer tables.
   For Cloud SQL, run this beside the database rather than over a long-distance local proxy. The one-time Cloud Run job `aibuildercup-seed` uses the API image with external workers and `python -c 'import hackathon.server;from hackathon.seed import seed;seed()'`; importing the dedicated entry point loads the approved runtime secret. It has no schedule and no automatic retries.
3. Use a dedicated Secret Manager secret. Grant the new service identity `roles/secretmanager.secretAccessor` on that secret only, plus the Cloud SQL connectivity role needed by the Cloud SQL integration. It must receive no production secret grants.
4. Build the API and frontend from this repository. `hackathon/Dockerfile.api`, `hackathon/Dockerfile.frontend` and the optional Cloud Build configuration are included. If Cloud Build submission is unavailable to the operator account, build the same Dockerfiles on the server under `/opt/aibuildercup/src`; do not replace existing product images.
5. Push the tested API image to Artifact Registry and deploy its immutable digest with `hackathon/deploy_cloudrun.sh`. That script names only the new service. It sets one warm instance, one maximum instance, four-request concurrency and a two-GiB memory limit. Inline workers process only this database's jobs.
6. Start the separate frontend using `hackathon/compose.yaml`, setting `DEMO_FRONTEND_IMAGE` to its tested image.
7. Back up and validate the existing HTTPS router configuration. Add only the `aibuildercup.aislemint.com` host: route `/api/*` to the new Cloud Run URL and all other paths to `aibuildercup-web:3000`. Set the upstream Host to the Cloud Run hostname and `X-Real-IP` to Caddy's `{remote_host}` so unrelated anonymous visitors do not share the proxy's rate bucket. Preserve all existing hosts. Validate before restarting/reloading the router and retain rollback copies.
8. Verify certificate issuance, guest entry in a fresh browser, independent workspaces, page/model data, model labels, confirmation→POS pricing, demo receipt and a reset. Probe the regular website, platform health and tracker afterward.

The main product release process can replace its shared router file. Preserve the additive demo host block during future router releases while judging is active. No recurring automation is installed by this repository.

## Access and limits

Visitors never enter credentials. `/api/demo/session` creates a short-lived anonymous synthetic identity; normal retailer permissions enforce ownership. `/api/demo/pos-session` unlocks only that visitor's synthetic register. The reset endpoint creates a new guest namespace and revokes the previous guest session.

Mode and parsed database connection-name checks fail closed. AI-key updates, membership/key management, provisioning/payment callbacks and external connector bindings are blocked. The app retains no SMTP, WhatsApp or payment-gateway credentials.

Application quotas are stored in the demo database and survive API restarts. They apply to guest creation, mutation volume, training and actual model attempts. A reset does not reset global quotas. Review cost and storage retention after judging; automatic deletion of old visitor workspaces is not enabled.

Opening another page with a valid synthetic guest session resumes the existing workspace without consuming creation or mutation quotas. Authentication still checks session expiry and revocation on every request. New/invalid sessions and explicit resets remain bounded. Browser Web Locks serialize first-time session creation across tabs so the cookie establishes one shared workspace before the other pages resume it.

## Recovery

- Roll the new Cloud Run service back to its previous tested image digest; do not change the production API service.
- Revert only the new router host block if routing fails, retaining the backup and existing host routes.
- Stop only the `aibuildercup` Compose project to disable the demo frontend.
- Do not drop the demo database as part of an ordinary restart. Re-seeding a completed template is idempotent and preserves visitor records. To regenerate a new baseline, first back up or deliberately recreate only the named demo database.
- Keep private runtime files under operator-controlled, ignored storage. No populated environment file, provider key, SQL password or service-account JSON belongs in Git.
