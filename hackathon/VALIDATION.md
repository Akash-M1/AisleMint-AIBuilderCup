# Validation record — 18 October 2026

## Completed locally

- **54 related domain regressions passed**, including AI boundaries, real POS pricing/returns and connected authorization. One unrelated external MongoDB adapter case was deselected: its old disposable container image cannot start on the host's current Docker kernel. Two existing Starlette deprecation warnings remain.
- **10 additional sandbox tests passed**: required demo mode, forbidden database names, conflicting `DATABASE_URL`, namespace transformations, deterministic synthetic histories and flyer/calendar conflict handling.
- TypeScript checking and the production frontend build passed.
- Two independent automatic guest sessions loaded promotion models, connected evidence, published analytics, OneSales products, shifts and ledger analytics. Cross-workspace requests were denied and public AI-key changes were blocked.
- A **15% MINT-OATS scenario was forecast, reviewed and confirmed through the actual API**. The same visitor's POS quote changed from **₹180 to ₹153 including GST**. No real payment was made.
- Published inventory matched the demand model's dated forecast origin. Reset produced a fresh namespace and the new session could not access its old workspace.
- The fixture produced actual trained candidates and passed the unchanged activation checks. Models use 156 weekly and 1,092 daily periods across 12 invented products. Fit metrics are synthetic fixture development results, not independent evidence of customer performance.
- One real image scan returned **two catalog-matched proposals using Gemini 3.8 Flash in 6.64 seconds**. An already approved chips offer was surfaced as a calendar conflict. Earlier tests exposed that conflict error and led to the explicit review-issue handling.
- One real merchant-copilot request returned **`gemini_agentic_copilot` with Gemini 3.8 Flash**, executed `inspect_operational_risks`, and passed citation-bound checks in **11.37 seconds**.
- Source scanning found no populated environment file, Google API-key pattern, private-key block or GitHub-token pattern in the publishable tree. This supplements the explicit source-only copy and ignored private-runtime paths; it is not a guarantee against every possible secret format.

All live model inputs were fictional flyer or synthetic store evidence. No customer data, bank alert, SMTP credential or payment information was submitted.

## Reproduce

```sh
.venv/bin/python -m pytest hackathon/tests -q
.venv/bin/python -m hackathon.seed /protected/demo.env
.venv/bin/python -m hackathon.verify /protected/demo.env
npm --prefix aislemint-platform/frontend run typecheck
npm --prefix aislemint-platform/frontend run build
```

The verifier creates new synthetic visitor sessions and confirms an oats offer inside its own namespace. It is an integration smoke test, not a read-only health probe. Keep it away from any other database; the application requires `aislemint_hackathon`.

## Public activation checks still required

At this preparation stage, the public deployment is waiting on runtime-secret/IAM approval. Record the following only after activation:

- HTTPS certificate and hostname routing.
- Fresh browser: no login, prepared sandbox, all mission links.
- Dedicated Cloud Run revision and database readiness.
- Live model badge and trace on the public deployment.
- Confirmation→POS→demo receipt and reset.
- A second visitor's independent data.
- Regular website, platform and tracker health after the additive router change.

Do not describe local timing as deployed latency or a single smoke test as a performance benchmark. Do not claim the entire historical regression suite passed; the measured selection and known fixture exclusion are stated above.
