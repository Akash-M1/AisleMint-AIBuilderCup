// Run inside the private YouPI image, with this file and billing server mounted
// at /review. Uses in-memory databases only; never sends a UPI transfer.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';
import { createPaymentApp } from '/app/apps/payment-api/src/index.js';
import { Database } from '/app/packages/db/src/database.js';
import { MerchantRepository } from '/app/packages/db/src/repositories/merchant.repository.js';
import { createBilling } from '/review/server.mjs';
import { GmailObserverServer } from '/app/apps/gmail-observer/src/server.js';

test('YouPI hardened pairing and signed observer requirement', async t => {
  process.env.AISLEMINT_OBSERVER_MODE = 'android';
  const db = new Database({ inMemory: true });
  const app = createPaymentApp(db); app.rawServer.listen(0, '127.0.0.1'); await once(app.rawServer, 'listening');
  t.after(() => { app.destroy(); app.rawServer.closeAllConnections(); app.rawServer.close(); db.close(); });
  const base = `http://127.0.0.1:${app.rawServer.address().port}`;
  assert.equal((await (await fetch(base + '/api/v1/checkout/server-health')).json()).isWorking, false);
  const post = (route, body, headers = {}) => fetch(base + route, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
  for (const enrollmentToken of ['pair_00000000', 'youpi_sms_pair_token_secure']) {
    assert.equal((await post('/api/v1/pollers/android/pair', { enrollmentToken })).status, 401);
  }
  const pair = await post('/api/v1/pollers/android/pair', { enrollmentToken: process.env.ANDROID_ENROLLMENT_SECRET });
  assert.equal(pair.status, 200); const device = await pair.json();
  assert.equal((await (await fetch(base + '/api/v1/checkout/server-health')).json()).isWorking, true);
  const observation = { deviceId: device.deviceId, body: 'not a bank alert' };
  assert.equal((await post('/api/v1/pollers/android/events', observation)).status, 401);
  assert.equal((await post('/api/v1/pollers/android/events', observation,
    { 'X-Timestamp': String(Date.now()), 'X-Signature': '0'.repeat(64) })).status, 401);
  assert.equal((await post('/api/v1/pollers/android/events', observation,
    { 'X-Timestamp': '1', 'X-Signature': '0'.repeat(64) })).status, 401);
});

test('Gmail OAuth uses loopback callback and checks the state cookie', async t => {
  const observer = new GmailObserverServer({ poller: { apiBaseUrl: 'http://127.0.0.1:1', credentials: { clientId: 'test.apps.googleusercontent.com' } } });
  const server = observer.createServer(); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const response = await fetch(base + '/oauth/begin', { redirect: 'manual' });
  assert.equal(response.status, 302); const target = new URL(response.headers.get('location'));
  assert.equal(target.hostname, 'accounts.google.com');
  assert.equal(target.searchParams.get('redirect_uri'), 'http://127.0.0.1:6003/oauth/callback');
  assert.equal(target.searchParams.get('state').length, 64);
  const cookie = response.headers.get('set-cookie').split(';')[0];
  assert.equal((await fetch(base + '/oauth/callback?code=fake&state=wrong', { headers: { Cookie: cookie } })).status, 403);
  assert.equal((await fetch(base + '/oauth/callback?state=' + target.searchParams.get('state'), { headers: { Cookie: cookie } })).status, 400);
});

test('real YouPI create/status/webhook contracts interoperate with billing', async t => {
  const db = new Database({ inMemory: true }), gateway = createPaymentApp(db);
  gateway.rawServer.listen(0, '127.0.0.1'); await once(gateway.rawServer, 'listening');
  const secret = 'isolated-contract-test-only-secret-32-plus';
  const billing = createBilling({ readinessCheck: async () => true, env: { BILLING_ENABLED: 'true', BILLING_DB_PATH: ':memory:', BILLING_PUBLIC_ORIGIN: 'http://127.0.0.1:4173',
    YOUPI_API_URL: `http://127.0.0.1:${gateway.rawServer.address().port}`, YOUPI_CHECKOUT_ORIGIN: 'https://aislemint.com',
    YOUPI_MERCHANT_ID: gateway.context.defaultMerchant.id, YOUPI_WEBHOOK_SECRET: secret } });
  billing.server.listen(0, '127.0.0.1'); await once(billing.server, 'listening');
  const base = `http://127.0.0.1:${billing.server.address().port}`;
  t.after(() => {
    gateway.destroy(); gateway.rawServer.closeAllConnections(); gateway.rawServer.close(); db.close();
    billing.server.closeAllConnections(); billing.server.close(); billing.db.close();
  });
  new MerchantRepository(db).update(gateway.context.defaultMerchant.id, { webhookSecret: secret, webhookUrl: base + '/api/billing/webhooks/youpi' });
  const catalog = await fetch(base + '/api/billing/plans'), cookie = catalog.headers.get('set-cookie').split(';')[0];
  const response = await fetch(base + '/api/billing/orders', { method: 'POST', headers: { Origin: 'http://127.0.0.1:4173', Cookie: cookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ planId: 'basic', name: 'Isolated Test', email: 'test@example.com', requestId: randomUUID(), acceptTestPurchase: true }) });
  assert.equal(response.status, 200); const order = await response.json();
  const session = gateway.context.sessionRepo.findByOrderId(gateway.context.defaultMerchant.id, order.orderId);
  assert.equal(Number(session.amountPaise), 1000); assert.equal(session.status, 'INITIATED');
  assert.equal(order.checkoutUrl, `https://aislemint.com/checkout/${session.id}`);
  // Synthetic final state ONLY in this isolated in-memory gateway. No bank or
  // production observer is contacted. Exercise its actual webhook signer.
  db.execute("UPDATE payment_sessions SET status='SUCCESS', completed_at=? WHERE id=?", [new Date().toISOString(), session.id]);
  const delivery = await gateway.context.webhookEngine.dispatch('SUCCESS', gateway.context.sessionRepo.findById(session.id));
  assert.equal(delivery.delivered, true);
  const status = await fetch(base + '/api/billing/orders/' + order.orderId, { headers: { Cookie: cookie } });
  assert.equal((await status.json()).status, 'SUCCESS');
});
