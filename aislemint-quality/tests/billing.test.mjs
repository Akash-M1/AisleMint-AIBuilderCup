import { test } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createHmac, randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { createBilling } from '../../aislemint-website/backend/server.mjs';

const origin = 'http://127.0.0.1:4173', secret = 'test-secret-which-is-longer-than-32-characters';
async function harness(t, overrides = {}, health = { phone: true, whatsapp: true }) {
  const sessions = new Map(); let creates = 0, fail = false, wrong = false;
  const dir = mkdtempSync(join(tmpdir(), 'am-billing-'));
  const env = { BILLING_PUBLIC_ORIGIN: origin, BILLING_ENABLED: 'true', BILLING_DB_PATH: join(dir, 'billing.sqlite'),
    YOUPI_API_URL: 'http://gateway:6100', YOUPI_CHECKOUT_ORIGIN: 'https://pay.example.com', YOUPI_MERCHANT_ID: 'merchant', YOUPI_WEBHOOK_SECRET: secret, BILLING_FULFILLMENT_ENABLED:'true', BILLING_CREDENTIAL_KEY:Buffer.alloc(32,7).toString('base64'), BILLING_PROVISIONING_SECRET:secret, SMTP_USER:'hello@aislemint.com', SMTP_FROM:'hello@aislemint.com', SMTP_PASSWORD:'fake-local-test', ...overrides };
  const fetchImpl = async (url, options) => {
    if (fail) throw Error('timeout');
    if (url.endsWith('/provisioning/status')) return Response.json({ready:true});
    if (url.endsWith('/admin/sms-gateway/devices')) return Response.json({ devices: health.phone ? [{ isActive: true, lastHeartbeatAt: new Date().toISOString() }] : [] });
    if (url.endsWith('/admin/whatsapp/status')) return Response.json({ isConnected: health.whatsapp });
    if (url.endsWith('/checkout/server-health')) return Response.json({ gmailHealthy: health.gmail === true, isWorking: health.gmail === true });
    if (options.method === 'POST') {
      creates++;
      const input = JSON.parse(options.body);
      assert.equal(options.headers['Idempotency-Key'], input.orderId);
      assert.equal(input.method, 'UPI'); assert.equal(input.autoActivate, undefined);
      const payment = sessions.get(input.orderId) || { orderId: input.orderId, paymentId: randomUUID(), amountPaise: String(Number(input.amount) * 100), currency: 'INR', method: 'UPI', status: 'INITIATED' };
      payment.checkoutUrl = `https://pay.example.com/checkout/${payment.paymentId}`;
      sessions.set(input.orderId, payment);
      return Response.json(wrong ? { ...payment, checkoutUrl: 'https://evil.example/checkout/' + payment.paymentId } : payment);
    }
    const payment = [...sessions.values()].find(p => url.endsWith(p.paymentId));
    return Response.json(payment, { status: payment ? 200 : 404 });
  };
  const app = createBilling({ env, fetchImpl, mailer:{verify:async()=>true}, evidenceReader:overrides.YOUPI_EVIDENCE_DB ? undefined : ()=>({reference:'629132688972',verifiedAt:new Date().toISOString()}) });
  app.server.listen(0, '127.0.0.1'); await once(app.server, 'listening');
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const plans = await fetch(base + '/api/billing/plans');
  const cookie = plans.headers.get('set-cookie').split(';')[0];
  const request = async (path, options = {}) => {
    const response = await fetch(base + path, { ...options, headers: { Origin: origin, Cookie: cookie, 'Content-Type': 'application/json', ...options.headers } });
    return { status: response.status, body: await response.json() };
  };
  const create = (data = {}, headers = {}) => request('/api/billing/orders', { method: 'POST', headers, body: JSON.stringify({ planId: 'basic', requestId: randomUUID(), name: 'Test Buyer', email: 'test@example.com', business:'Test Shop', acceptAnnualPurchase:true, pricingVersion:'annual-2026-10', ...data }) });
  const webhook = async (order, data = {}, headers = {}) => {
    const payment = sessions.get(order.orderId);
    const raw = JSON.stringify({ event: 'SUCCESS', paymentId: payment.paymentId, orderId: order.orderId, amountPaise: Number(payment.amountPaise), currency: 'INR', method: 'UPI', ...data });
    return request('/api/billing/webhooks/youpi', { method: 'POST', body: raw, headers: {
      'X-Gateway-Signature': 'sha256=' + createHmac('sha256', secret).update(raw).digest('hex'), ...headers } });
  };
  t.after(async () => { app.server.closeAllConnections(); await new Promise(resolve => app.server.close(resolve)); app.closeEvidence(); app.db.close(); rmSync(dir, { recursive: true, force: true }); });
  return { app, request, create, webhook, sessions, plans: await plans.json(), cookie,
    setFail: value => { fail = value; }, setWrong: value => { wrong = value; }, creates: () => creates };
}
test('catalog exposes exact annual INR prices and manual renewal', async t => {
  const h = await harness(t); assert.deepEqual(h.plans.plans.map(p => p.amountPaise), [1500000, 2500000, 5000000]);
  assert.equal(h.plans.checkoutEnabled, true); assert.equal(h.plans.pricing, 'annual-2026-10');
});
test('server ignores a tampered price and creates each correct plan amount', async t => {
  const h = await harness(t);
  for (const [planId, amountPaise] of [['basic', 1500000], ['premium', 2500000], ['ultra', 5000000]]) {
    const r = await h.create({ planId, amountPaise: 1, amount: '0.01' }); assert.equal(r.status, 200); assert.equal(r.body.amountPaise, amountPaise);
  }
});
test('idempotent retries and concurrent clicks reuse one order and gateway session', async t => {
  const h = await harness(t), requestId = randomUUID();
  const [a, b] = await Promise.all([h.create({ requestId }), h.create({ requestId })]);
  assert.equal(a.status, 200); assert.equal(a.body.orderId, b.body.orderId); assert.equal(h.creates(), 1);
  assert.equal(h.app.db.prepare('SELECT COUNT(*) AS n FROM orders').get().n, 1);
});
test('browser origin, consent, invalid plans and email are checked', async t => {
  const h = await harness(t);
  assert.equal((await h.create({}, { Origin: 'https://evil.example' })).status, 403);
  for (const input of [{ acceptAnnualPurchase: false }, {pricingVersion:'test-2026-10'}, {business:''}, { planId: 'free' }, { email: 'bad' }, { name: 'x' }])
    assert.equal((await h.create(input)).status, 422);
});
test('other browser cannot read orders or reuse idempotency keys', async t => {
  const h = await harness(t), requestId = randomUUID(), r = await h.create({ requestId });
  const alien = { Cookie: 'am_checkout=' + 'a'.repeat(64) };
  assert.equal((await h.request('/api/billing/orders/' + r.body.orderId, { headers: alien })).status, 404);
  assert.equal((await h.create({ requestId }, alien)).status, 409);
});
test('unsigned webhook and mismatched amounts or payment IDs never mark paid', async t => {
  const h = await harness(t), r = await h.create();
  assert.equal((await h.webhook(r.body, {}, { 'X-Gateway-Signature': 'sha256=bad' })).status, 401);
  assert.equal((await h.webhook(r.body, {}, { 'X-Gateway-Signature': 'sha256=' + 'ü'.repeat(64) })).status, 401);
  assert.equal((await h.webhook(r.body, { amountPaise: 1 })).status, 409);
  assert.equal((await h.webhook(r.body, { paymentId: randomUUID() })).status, 409);
  assert.equal((await h.request('/api/billing/orders/' + r.body.orderId)).body.status, 'PENDING');
});
test('a signed event also requires matching authoritative gateway state', async t => {
  const h = await harness(t), r = await h.create();
  assert.equal((await h.webhook(r.body)).status, 409);
  h.sessions.get(r.body.orderId).status = 'SUCCESS';
  assert.equal((await h.webhook(r.body)).status, 200);
  const order = (await h.request('/api/billing/orders/' + r.body.orderId)).body;
  assert.equal(order.status, 'SUCCESS'); assert.ok(order.paidAt); assert.equal(order.onboarding, 'automatic'); assert.equal(order.email, 'test@example.com'); assert.ok(order.fulfillment.receiptId); assert.equal(order.fulfillment.bankReference,'629132688972');
});
test('replayed events are idempotent and success cannot be downgraded', async t => {
  const h = await harness(t), r = await h.create(), payment = h.sessions.get(r.body.orderId);
  payment.status = 'SUCCESS';
  await h.webhook(r.body); await h.webhook(r.body);
  assert.equal(h.app.db.prepare('SELECT COUNT(*) AS n FROM events').get().n, 1);
  payment.status = 'FAILED'; await h.webhook(r.body, { event: 'FAILED' });
  assert.equal((await h.request('/api/billing/orders/' + r.body.orderId)).body.status, 'SUCCESS');
});
test('browser status query and callback parameters cannot promote success', async t => {
  const h = await harness(t), r = await h.create(); h.sessions.get(r.body.orderId).status = 'SUCCESS';
  assert.equal((await h.request('/api/billing/orders/' + r.body.orderId + '?status=SUCCESS')).body.status, 'PENDING');
});
test('expired, cancelled, failed and suspicious outcomes are exposed accurately', async t => {
  const h = await harness(t);
  for (const status of ['EXPIRED', 'CANCELLED', 'FAILED', 'SUSPICIOUS']) {
    const r = await h.create(); h.sessions.get(r.body.orderId).status = status;
    assert.equal((await h.request('/api/billing/orders/' + r.body.orderId)).body.status, status);
  }
});
test('late verified success supersedes expiry', async t => {
  const h = await harness(t), r = await h.create(), payment = h.sessions.get(r.body.orderId);
  payment.status = 'EXPIRED'; await h.request('/api/billing/orders/' + r.body.orderId);
  payment.status = 'SUCCESS'; assert.equal((await h.webhook(r.body)).status, 200);
  assert.equal((await h.request('/api/billing/orders/' + r.body.orderId)).body.status, 'SUCCESS');
});
test('provider outage retains order and retry resumes safely', async t => {
  const h = await harness(t), requestId = randomUUID(); h.setFail(true);
  assert.equal((await h.create({ requestId })).status, 502); h.setFail(false);
  assert.equal((await h.create({ requestId })).status, 200);
  assert.equal(h.app.db.prepare('SELECT COUNT(*) AS n FROM orders').get().n, 1);
});
test('unexpected checkout origin is rejected', async t => {
  const h = await harness(t); h.setWrong(true); assert.equal((await h.create()).status, 502);
});
test('unconfigured checkout fails closed without creating orders', async t => {
  const h = await harness(t, { BILLING_ENABLED: 'false' });
  assert.equal(h.plans.checkoutEnabled, false); assert.equal((await h.create()).status, 503);
  assert.equal(h.app.db.prepare('SELECT COUNT(*) AS n FROM orders').get().n, 0);
});
test('checkout stops when the Android observer or buyer OTP channel is offline', async t => {
  for (const health of [{ phone: false, whatsapp: true }, { phone: true, whatsapp: false }]) {
    const h = await harness(t, {}, health);
    assert.equal(h.plans.checkoutEnabled, false); assert.equal((await h.create()).status, 503);
    assert.equal(h.app.db.prepare('SELECT COUNT(*) AS n FROM orders').get().n, 0);
  }
});
test('purchase records survive database reopen', async t => {
  const h = await harness(t); const r = await h.create();
  const reopened = createBilling({ env: { BILLING_DB_PATH: h.app.db.location() } });
  assert.equal(reopened.db.prepare('SELECT plan_id FROM orders WHERE id=?').get(r.body.orderId).plan_id, 'basic'); reopened.db.close();
});
test('Gmail mode uses the selected channel and does not require Android', async t => {
  const h = await harness(t, { BILLING_OBSERVER_MODE: 'gmail' }, { phone: false, gmail: true, whatsapp: true });
  assert.equal(h.plans.checkoutEnabled, true); assert.equal((await h.create()).status, 200);
  const offline = await harness(t, { BILLING_OBSERVER_MODE: 'gmail' }, { phone: true, gmail: false, whatsapp: true });
  assert.equal(offline.plans.checkoutEnabled, false); assert.equal((await offline.create()).status, 503);
});

test('live success requires a matching non-simulated receiver observation with a UTR', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'am-evidence-')), path = join(dir, 'gateway.sqlite');
  const evidence = new DatabaseSync(path);
  evidence.exec(`CREATE TABLE payment_sessions(id,order_id,amount_paise,currency,status,completed_at,attributed_observation_id);
    CREATE TABLE payment_observations(id,bank_reference_number,raw_payload);`);
  t.after(() => { evidence.close(); rmSync(dir, { recursive:true, force:true }); });
  const h = await harness(t, { YOUPI_EVIDENCE_DB:path });
  const r = await h.create(), payment = h.sessions.get(r.body.orderId); payment.status = 'SUCCESS';
  assert.equal((await h.webhook(r.body)).status, 409);
  evidence.prepare('INSERT INTO payment_sessions VALUES(?,?,?,?,?,?,?)').run(payment.paymentId,r.body.orderId,1500000,'INR','SUCCESS',new Date().toISOString(),'obs');
  evidence.prepare('INSERT INTO payment_observations VALUES(?,?,?)').run('obs','629132688972','{"is_simulated":true}');
  assert.equal((await h.webhook(r.body)).status, 409);
  evidence.exec("UPDATE payment_observations SET raw_payload='{}',bank_reference_number='bad'");
  assert.equal((await h.webhook(r.body)).status, 409);
  evidence.exec("UPDATE payment_observations SET bank_reference_number='629132688972'");
  assert.equal((await h.webhook(r.body)).status, 200);
  assert.equal(h.app.fulfillment.publicState(r.body.orderId).bankReference,'629132688972');
});

test('old test-price successes remain historical and never enroll an annual plan', async t => {
  const h = await harness(t), r = await h.create(), payment = h.sessions.get(r.body.orderId);
  h.app.db.prepare("UPDATE orders SET pricing_version='test-2026-10',amount_paise=1000 WHERE id=?").run(r.body.orderId);
  payment.amountPaise='1000';payment.status='SUCCESS';
  assert.equal((await h.webhook(r.body)).status,200);
  const order=(await h.request('/api/billing/orders/'+r.body.orderId)).body;
  assert.equal(order.amountPaise,1000);assert.equal(order.onboarding,'deferred');assert.equal(order.fulfillment,null);
});
