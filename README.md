# AisleMint — AI Builder Cup

**A competitor cuts prices. What should a small retailer do next?**

AisleMint combines Gemini interpretation with retail forecasting, constrained promotion planning, connected operational evidence and exact point-of-sale calculations. This repository contains the complete application source used by the isolated hackathon simulation.

**Live demo:** [aibuildercup.aislemint.com](https://aibuildercup.aislemint.com). Public HTTPS, anonymous guest workspaces, the Gemini flyer scan, promotion confirmation, checkout receipt and reset have been verified.

Demo domain: **https://aibuildercup.aislemint.com**. Deployment status is recorded in [the deployment notes](hackathon/DEPLOYMENT.md). No sign-in or purchase is required: the app creates an anonymous, browser-scoped synthetic workspace automatically.

## Try the mission

1. Open **Operating picture** and inspect the oats shelf: 8 packs against a reorder point of 25. Follow product → supplier → overdue order.
2. Open the [fictional flyer](aislemint-platform/frontend/public/demo-flyer.png), then scan it in **Scenario studio**. Gemini extracts and matches catalog items. Existing calendar conflicts are shown for review.
3. Simulate a **15% discount on MINT-OATS for one week**, review the financial assumptions, and confirm the promotion.
4. Open **OneSales**. The same oats SKU changes from **₹180 to ₹153 including GST** when that offer is active on the simulation reference date. The cashier UI opens automatically in the demo.
5. Use **AI Shopper & Deal Concierge** to request a snack basket under **₹500**. The deterministic checkout engine verifies stock, offers, tax and budget.
6. Explore demand/inventory planning, business analytics, model cards, shared data releases, the ontology, the quarantined bad feed and reviewed operational decisions.

**Start fresh** creates a new sandbox for your browser. Other visitors' workspaces are inaccessible. There are no production accounts, payment records or customer datasets in this deployment.

## What's implemented

- Gemini flyer image/text extraction, structured basket suggestions, tool-calling merchant explanations and campaign drafts.
- Gradient-boosted promotion-response and direct multi-horizon demand models, chronological evaluation and owner-controlled activation gates.
- SciPy/HiGHS promotion optimization and reviewable calendar execution.
- Connected products, inventory, suppliers, purchase orders, shipments, documents, lineage and quarantine.
- Immutable shared data products, business analytics and inventory planning.
- OneSales checkout, receipts, returns, stock movement, cash shifts and integer-paise GST calculations.
- Explicit model/fallback labels, budget and stock checks, catalog validation and evidence citation bounds.

## Simulation, not customer performance

All 12 products, prices, suppliers, stock, transactions, promotion responses and 156 weekly / 1,092 daily training periods are **invented**. A fixed random seed makes them reproducible. The simulation includes 24 checkout transactions calculated through the real POS rules and 90 days of synthetic sales published into the shared data product.

Both models are actually trained and pass the existing activation policy on this synthetic fixture. Fixture development involved repeated local iterations; those metrics **do not establish independent model quality, causal promotion lift or real retailer ROI**. Each browser receives its own registry and operational records referencing the same immutable synthetic fitted artifacts. This avoids retraining on every page load; it does not claim a fresh model fit per visitor.

The demo uses a labelled, fixed promotion reference date for repeatable checkout handoffs. New register shifts use the visitor's current India trading day. Forecast horizons remain tied to the generated model snapshot.

No supplier order, email, WhatsApp message, real payment or external ERP write is sent. Campaign output is a draft. Proposed supplier funding is conditional, not agreed revenue.

## One repository, modular source

| Directory | Purpose |
|---|---|
| `aislemint-platform` | React/vinext application shell |
| `aislemint-core` | Shared runtime, persistence, UI, Gemini gateway and sandbox handlers |
| `aislemint-promotions` | Promotion forecasting, optimization, calendar and flyer workflows |
| `aislemint-demand` | Demand forecasts and inventory planning |
| `aislemint-sales` | OneSales, returns and ledger analytics |
| `aislemint-data` | Data products, modelling workbench and evaluated model registry |
| `aislemint-connect` | Connections, ontology, ingestion, evidence and decisions |
| `aislemint-admin` | Existing account and product permission boundaries |
| `aislemint-ops` | Dependency locks and local composition loader |
| `aislemint-quality` | Domain regression source |
| `hackathon` | Synthetic generator, isolated entry point, containers, verifier and presentation |

This is a snapshot of the owner's authorized AisleMint source plus hackathon-specific development. [Source manifest](hackathon/source-manifest.json) records original repository revisions; local additions are included. The owner reports organizer confirmation that extending this project is eligible. This repository has no dependency on cloning the private original repositories.

## Run an isolated copy

Requirements: Python 3.13, Node 22+, PostgreSQL 18 and a dedicated empty database named **`aislemint_hackathon`**. Create an unprivileged database owner for this database only. Never point it at the commercial deployment.

```sh
python3 -m venv .venv
.venv/bin/pip install -r aislemint-ops/requirements-model-lab.lock.txt
python3 hackathon/bootstrap.py
```

Put private settings outside Git (for example `.release/local.env`):

```dotenv
AISLEMINT_HACKATHON=1
POSTGRES_DATABASE=aislemint_hackathon
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=YOUR_DEMO_DB_USER
POSTGRES_PASSWORD=YOUR_DEMO_DB_PASSWORD
POSTGRES_SSLMODE=disable
DEMO_LOCAL=1
PUBLIC_ORIGIN=http://127.0.0.1:3000
PUBLIC_ORIGINS=http://localhost:3000
TRUSTED_HOSTS=localhost,127.0.0.1,testserver
RETAIL_TRAINING_WORKER=inline
CONNECTED_WORKER=inline
DATA_PLATFORM_WORKER=inline
LEGACY_TRAINING_WORKER=external
GEMINI_API_KEY=YOUR_PRIVATE_GOOGLE_AI_KEY
```

```sh
# Seeds only the explicitly named demo database; reruns preserve a completed template.
.venv/bin/python -m hackathon.seed /absolute/path/to/private.env
# Verifies two guest sessions, tenant isolation, models, analytics, POS, confirmation and reset.
.venv/bin/python -m hackathon.verify /absolute/path/to/private.env
# Starts the API on localhost:8108; set DEMO_PORT to change it.
.venv/bin/python hackathon/start_local.py /absolute/path/to/private.env
# In another terminal:
AISLEMINT_API_PROXY=http://127.0.0.1:8108 npm --prefix aislemint-platform/frontend run dev
```

The development frontend proxies `/api` to the API. In production, the HTTPS router sends `/api/*` to the dedicated Cloud Run service and all other paths to the separate frontend container.

Run the demo-only boundary tests without any database:

```sh
.venv/bin/python -m pytest hackathon/tests -q
npm --prefix aislemint-platform/frontend run typecheck
npm --prefix aislemint-platform/frontend run build
```

The broader historical domain tests under `aislemint-quality` also require their documented disposable test fixtures. They do not run against a deployed demo or customer database.

## Public sandbox limits

Automatic guest sessions retain normal tenant authorization. The demo refuses a different database name, including a conflicting `DATABASE_URL`. Account provisioning, AI-key replacement, real payment callbacks, external connector bindings and membership/key management are disabled. Only synthetic push feeds and the demo's own OneSales connector are available.

Limits include 200 new guest workspaces/day, 2,000 demo mutations/day, 200 model calls/day, 24 model calls per visitor/hour, and 12 training requests/day. Generation has at most two application attempts, 2,048 output tokens per attempt, three tool rounds and six tool executions. A quota or provider outage produces a labelled fallback or retry response. Limits bound calls; they are not a guaranteed currency budget.

## Present it

- [Three-minute video script and exact click sequence](hackathon/DEMO_SCRIPT.md)
- [Architecture, deployment and recovery](hackathon/DEPLOYMENT.md)
- [Validation record](hackathon/VALIDATION.md)

Do not record private cloud consoles, keys or real bank alerts. Keep synthetic labels visible, read actual on-screen results, and distinguish proposed funding and projected demand from observed business outcomes.
