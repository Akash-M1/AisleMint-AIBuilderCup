# AisleMint: the match-day decision

Use **one retailer, one product identity and one story**. The hook is not the number of screens. It is that the recommendation survives a handoff into a real pricing calculation.

## Before recording

- Open the deployed demo in a full-width desktop browser, ideally 1440×900 or 1920×1080. Use 100% zoom and hide notifications.
- Click **Start fresh** once. This is a synthetic sandbox; it does not clear anyone else's session.
- Download/open the supplied **sample flyer PNG**. Keep the file chooser directory ready. Pasting the supplied text is a reliable alternative; say “text extraction” if using text.
- Have tabs ready for `/demo`, `/connect`, `/promotions/scenarios`, `/demand/inventory`, `/sales/pos`, `/data/models` and `/sales/analytics` on the same demo hostname. They share your sandbox cookie.
- Rehearse the 15% oats confirmation once, then start fresh for the recording. Oats starts at ₹180. The expected price after that specific offer is ₹153 including GST. Chips has a separately labelled pre-approved demo special, so an attempted overlapping chips offer should be flagged.
- Verify the actual model badge after a scan. A cached response may be reused for 15 minutes. Do not describe a replay as a new live invocation.
- Record a clean core video before attempting the optional extended walkthrough. Keep a backup recording. Check the current participant-dashboard duration limit before exporting.

## Core video: about three minutes

Speak conversationally. Keep the synthetic-data label visible and read current results, not memorized profit projections.

| Time | Screen and action | Voiceover |
|---|---|---|
| 0:00–0:18 | `/demo`; pause on the mission and the 8-pack / 25% / ₹500 card | “A competitor drops prices before match day. A small retailer needs to decide what to discount, whether the margin works, and whether the shelf can support it. AisleMint connects that decision to the checkout. This is our fully synthetic MintMart simulation.” |
| 0:18–0:40 | `/connect` → inspect Honey Oats; show its low stock and linked supplier/order | “The oats shelf has eight packs against a reorder point of twenty-five, with delayed replenishment evidence. These are explicit operational records. We can follow the product to its supplier and purchase order rather than ask an AI to guess what is happening.” |
| 0:40–1:08 | `/promotions/scenarios` → upload the supplied PNG or paste the sample → scan; show model label and matched oats | “Gemini reads the competitor flyer and maps its claims to our exact catalog IDs. The software rejects unknown products. Chips already has a conflicting demo offer, so that conflict is surfaced for review while other candidates continue.” |
| 1:08–1:38 | Select **MINT-OATS**, **15%**, **one week**; run the scenario; show warnings and results; name it and **Confirm promotion** | “The language model proposes; the demand engine calculates an estimated scenario. Here I review a fifteen-percent oats offer. Supplier support is conditional, and the forecast shows its assumptions. I explicitly confirm the decision before it enters the promotion calendar.” |
| 1:38–1:56 | `/demand/inventory`; show the same SKU, forecast and dated stock | “The same catalog and approved calendar feed demand and inventory planning. The stock position is dated. Missing stock is not silently invented, and projected demand is not presented as an observed outcome.” |
| 1:56–2:34 | `/sales/pos`; add one oats pack; show **₹153**; open **AI Shopper & Deal Concierge**, request a ₹500 snack basket and load it | “Now the handoff: this same oats product was one hundred and eighty rupees. With the confirmed fifteen-percent offer, checkout charges one hundred and fifty-three, including GST. The AI shopper can propose a five-hundred-rupee basket, but code checks stock, promotions, tax and the final budget.” |
| 2:34–2:55 | `/data/models` or analytics, then return to `/demo` | “We retain model evaluation, source lineage and operational evidence. Every trading record here is simulated; this is not a claim of customer ROI. Our contribution is the connection between flexible Gemini interpretation, auditable retail calculations and human approval. The judges can explore their own sandbox without signing in.” |

The spoken copy is deliberately short enough to leave time for clicks and a model response. If you are over three minutes, omit the inventory scene from the core recording and include it in the extended demo. Do not speed the recording up so much that totals or warnings cannot be read.

## Exact interaction notes

### 1. Operating picture

Follow **Start the guided mission**. Open Honey Oats or its low-stock evidence. Show an actual resolved supplier/order edge and its revision/provenance if available. The raw risk count can include both Product and InventoryPosition records for one SKU; do not equate it with a count of unique products.

Optional Evidence assistant prompt:

> Which products have low stock? Use inspect_operational_risks and cite the numbered evidence. Do not optimize promotions.

If using Gemini mode, show its actual mode/model and tool trace. A deterministic evidence result remains useful, but must be described as evidence search if that is what the UI returns.

### 2. Flyer scanner

Use the supplied PNG. It is clearly labelled as a fictional competitor advertisement:

> Weekend Match Day: 25% OFF Honey Oats 500g; 20% OFF Mango Juice 1L; 15% OFF Masala Chips 100g.

An observed local test returned two valid counter-offers with `gemini-3.8-flash` in approximately 6.6 seconds; the existing chips campaign was a review conflict. That is one measurement, not a latency guarantee. Model-selected discounts can differ between runs. Read the current cards.

Do not say a funded scenario is guaranteed profitable. Say: “Under the stated simulated assumptions and proposed supplier allowance, the model estimates this result.”

### 3. The approval-to-checkout proof

Use **Honey Oats / MINT-OATS**, 15%, one week. Run it and click **Confirm promotion**, not only **Save**. Saving is a draft and does not activate a checkout rule. If a stale-model or calendar warning appears, rerun the scenario and review again.

Open OneSales and add one oats pack. Expect ₹153 for that exact offer. If you chose a different discount, read the resulting price instead of quoting ₹153. The demo shows a fixed simulation planning date so the same recorded scenario can be demonstrated later.

For the shopper, paste:

> Build a match-day snack basket with juice and chips under ₹500. Prefer active offers and stay within stock.

Enter **500** in the explicit budget field. Natural language alone is not the authoritative budget input. Show **Checkout total** and **Load into Live Register**. “Live Register” here is the real checkout engine operating on synthetic data; no money is collected.

### 4. Receipts and returns (optional)

Use a simulated **cash** tender for the exact basket total and complete the sale. Open the marked demo receipt. Do not launch a real UPI payment. For a return, select the complete linked offer group when required and show the deterministic refund calculation. Receipts are proof of software calculations on synthetic inputs, not evidence of a real financial transaction.

## Extended walkthrough: 6–8 minutes

After the core story, add these proof points:

1. **Pipelines:** open the separate quality-lab source and its quarantined malformed stock value. Explain that bad evidence is rejected rather than silently becoming inventory. This source is excluded from the authoritative shared data product.
2. **Ontology:** traverse Product → Supplier and PurchaseOrder → Product. Show object revision and source lineage.
3. **Decisions:** review the pre-seeded oats replenishment proposal, add a note and approve it. Explain that this records a decision; it does not place an external supplier order.
4. **Campaign studio:** generate a supplier negotiation draft and multilingual customer copy. Show the conditional funding amount. Do not send the draft or claim the supplier accepted it.
5. **Modelling workbench:** open promotion and demand model cards, chronological folds, naive comparisons, coverage and activation policy. Say these fits use simulated history and were developed against this fixture.
6. **Business analytics:** switch between the 90-day shared synthetic data product and the OneSales synthetic ledger. They are separate demonstration datasets with matching catalog identity; the 24 POS receipts are not the source of all 1,092 training days.
7. **Independent visitor:** open a private browser session or show another browser. Its workspace starts fresh. A reset in one browser must not change another visitor's work.

## Explain the architecture in one breath

“React provides the retail workspace. A dedicated Google Cloud Run API serves the hackathon copy, with a separate PostgreSQL database and database user. Gemini handles interpretation and explanations. Python forecasting and optimization engines calculate scenarios, and OneSales executes exact checkout rules. A separate frontend on the AisleMint server uses the hackathon subdomain. We reuse no customer data.”

This deployment has been verified at **https://aibuildercup.aislemint.com**. The public validation record includes the dedicated Cloud Run revision and actual guest, model, checkout and reset checks.

## Likely judge questions

**What is the innovation?**
“Closing the gap between an AI suggestion and a retail decision that has to survive catalog, calendar, stock, budget and checkout constraints. The integration and its refusal behavior are demonstrated in the product.”

**Why use Gemini?**
“Flyers and shopping requests are unstructured. Gemini turns them into catalog-linked proposals and explanations. It does not replace the financial or checkout engine.”

**Is promotion uplift causal?**
“No. These are observational model estimates on simulated data. A real pilot needs prospective validation and, where feasible, a controlled evaluation.”

**Do you send supplier orders or customer campaigns automatically?**
“No. The prototype produces reviewable drafts and recorded approvals. It sends no external order or message in this sandbox.”

**Did every visitor train a new model?**
“No. Visitors get isolated operational records and registry identities referencing the same immutable synthetic fitted template. New candidate training uses the modelling workflow.”

**How do you protect the real product?**
“The demo has its own repository, containers/service identity, database and unprivileged database login. Guest permissions are scoped to their own synthetic retailer. Account/key administration and external credentials are unavailable in the public demo.”

**What have you measured?**
“Software boundary regressions, an end-to-end confirmation-to-POS check, synthetic model holdouts, and specific live model smoke tests. These are not real customer revenue results.”

## Delivery style

Start with the retailer's problem, keep the cursor still while explaining a number, and say what each handoff proves. Pause briefly at the conflict warning, the confirmation and the ₹153 total. Those are stronger proof than a fast tour of every page. Finish with the judge URL and one concrete next step: a prospective retailer pilot.
