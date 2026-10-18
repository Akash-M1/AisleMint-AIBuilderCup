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

## Public deployment verified

- HTTPS certificate and routing passed at **https://aibuildercup.aislemint.com**. Dedicated Cloud Run revision: `aibuildercup-api-00001-g8p`.
- The one-time in-region Cloud Run seed job completed successfully. Both models passed the unchanged activation gates; the separate database contains the fictional store and resettable baseline.
- A fresh browser opened the mission without sign-in. Connected intelligence rendered 8 sources and 1,122 business objects. Oats resolved to its fictional supplier, delayed replenishment, documents and source lineage.
- Two independent public HTTP sessions loaded promotion/demand summaries, connected evidence, published analytics and OneSales. Preparation plus those checks took 6.07 and 5.92 seconds in this smoke run. Cross-visitor requests and AI-key changes were denied.
- The public API forecast and confirmed the 15% oats offer. Its checkout total changed from ₹180 to **₹153 including GST**, and an exact-value simulated cash tender created a full receipt. No money was collected.
- A real public-deployment flyer call returned `ai_powered=true`, **Gemini 3.8 Flash**, and one valid remaining proposal in **7.29 seconds**. This was tested after oats was confirmed, so oats and the pre-approved chips offer could conflict; earlier local pre-confirmation tests had two proposals. Results vary with workspace calendar state and model output.
- The public merchant assistant returned `gemini_agentic_copilot` with **Gemini 3.8 Flash**, a recorded `inspect_operational_risks` tool call and numbered evidence in **7.82 seconds**. The public AI shopper used **Gemini 3.5 Flash-Lite** and produced a code-priced **₹408** basket against an explicit ₹500 budget in **1.86 seconds**. These are individual smoke measurements, not latency guarantees.
- Browser inspection confirmed that OneSales opens an active synthetic register without a PIN prompt and displays the catalog, fixed simulation date and AI shopper controls.
- Reset created a new guest, and the new session was denied access to its old workspace.
- The initial public smoke test exposed orphan counters from an interrupted seed setup. They were backed up and excluded from the resettable template; the exporter now selects only baseline-scoped counters. The successful two-guest test above followed that repair.
- The demo proxy forwards the visitor address for anonymous-session quotas, avoiding a shared proxy-address bucket for unrelated judges.
- Original website, platform health and tracker each returned HTTP **200** after the additive route activation. Their application images and customer data were not replaced.

Do not describe local timing as deployed latency or a single smoke test as a performance benchmark. Do not claim the entire historical regression suite passed; the measured selection and known fixture exclusion are stated above.
