import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac, randomUUID } from 'node:crypto';
import { createBilling } from '../../aislemint-website/backend/server.mjs';
import { createFulfillment } from '../../aislemint-website/backend/fulfillment.mjs';

function harness(t, overrides = {}) {
  const env = { BILLING_DB_PATH: ':memory:', BILLING_FULFILLMENT_ENABLED: 'true',
    BILLING_CREDENTIAL_KEY: Buffer.alloc(32, 5).toString('base64'), BILLING_PROVISIONING_SECRET: 'isolated-secret-longer-than-32-characters',
    SMTP_USER: 'akashm@aislemint.com', SMTP_FROM: 'hello@aislemint.com', SMTP_PASSWORD: 'fake-smtp-password' };
  let time = Date.now(), failures = new Set(), provisions = 0, newAccount = true;
  const sent = [], payloads = [], results = new Map();
  const mailer = { verify: async () => { if (failures.has('verify')) throw Error(); return true; }, sendMail: async message => {
    if (failures.has(message.messageId.includes('receipt.') ? 'receipt' : 'welcome')) throw Error('private SMTP response must never be logged');
    sent.push(message); return { accepted: ['buyer@example.test'], rejected: [] };
  } };
  const fetchImpl = async (url, options) => {
    const stamp = options.headers['X-Aislemint-Timestamp'];
    assert.equal(options.headers['X-Aislemint-Signature'], createHmac('sha256', env.BILLING_PROVISIONING_SECRET).update(stamp + '.' + options.body).digest('hex'));
    if (url.endsWith('/status')) return Response.json({ ready: !failures.has('health') });
    if (failures.has('provision')) throw Error('offline');
    if (failures.has('review')) return Response.json({}, { status: 409 });
    const input = JSON.parse(options.body); payloads.push(input); provisions++;
    const result = results.get(input.order_id) || { order_id: input.order_id, retailer_id: randomUUID(), shop_id: randomUUID(),
      new_account: newAccount, starts_at: new Date(time).toISOString(), expires_at: new Date(time + 365 * 86400000).toISOString(),
      temporary_password_expires_at: new Date(time + 72 * 3600000).toISOString() };
    results.set(input.order_id, result);
    if (failures.has('lost-response')) throw Error('response lost after commit');
    return Response.json(result);
  };
  const app = createBilling({ env: { ...env, ...overrides }, mailer, fetchImpl, clock: () => time });
  t.after(() => app.db.close());
  function order(version = 'annual-2026-10') {
    const id = 'AM-' + randomUUID(), timestamp = new Date(time).toISOString();
    app.db.prepare(`INSERT INTO orders(id,request_id,session_hash,plan_id,amount_paise,name,email,business,status,payment_id,created_at,updated_at,paid_at,pricing_version)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(id, randomUUID(), 'test-session', 'basic', version.startsWith('annual') ? 1500000 : 1000,
      'Buyer <script>alert(1)</script>', 'buyer@example.test', 'Shop & <Partners>', 'SUCCESS', randomUUID(), timestamp, timestamp, timestamp, version);
    const row = app.db.prepare('SELECT * FROM orders WHERE id=?').get(id);
    app.fulfillment.enqueue(row, { reference: '629132688972', verifiedAt: timestamp }); return row;
  }
  return { ...app, env, order, sent, payloads, mailer, fetchImpl, failures, provisions: () => provisions,
    advance(ms = 3600001) { time += ms; }, existing() { newAccount = false; } };
}

test('verified annual purchase creates one receipt, workspace and invitation across duplicate jobs', async t => {
  const h = harness(t), o = h.order();
  h.fulfillment.enqueue(o, { reference: '629132688972' });
  await h.fulfillment.tick(); await h.fulfillment.tick();
  assert.equal(h.provisions(), 1); assert.equal(h.sent.length, 2);
  assert.deepEqual(h.sent.map(m => m.from.address), ['hello@aislemint.com', 'hello@aislemint.com']);
  const state = h.fulfillment.publicState(o.id);
  assert.equal(state.accountStatus, 'ready'); assert.equal(state.receiptEmail, 'sent'); assert.equal(state.welcomeEmail, 'sent');
  assert.ok(h.sent[0].text.includes('₹15,000.00')); assert.ok(h.sent[0].text.includes('629132688972'));
  assert.ok(h.sent[0].text.includes(o.payment_id)); assert.ok(h.sent[0].text.includes('annual'));
  assert.ok(!h.sent[0].html.includes('<script>')); assert.ok(h.sent[0].html.includes('&lt;Partners&gt;'));
  assert.ok(h.sent[1].text.includes(h.payloads[0].temporary_password));
  assert.ok(h.sent[1].html.includes(h.payloads[0].temporary_password));
  assert.ok(h.sent[1].text.includes('https://aislemint.com/reset-password.html'));
  assert.ok(!JSON.stringify(state).includes(h.payloads[0].temporary_password));
  assert.equal(h.db.prepare('SELECT credential_cipher FROM fulfillments').get().credential_cipher, null);
});

test('legacy successful test orders never generate paid access or emails', async t => {
  const h = harness(t); h.order('test-2026-10'); await h.fulfillment.tick();
  assert.equal(h.provisions(), 0); assert.equal(h.sent.length, 0);
  assert.equal(h.db.prepare('SELECT count(*) n FROM fulfillments').get().n, 0);
});

test('SMTP outage retains encrypted invitation, retries delivery and never recreates the account', async t => {
  const h = harness(t); h.failures.add('receipt'); h.failures.add('welcome'); const o = h.order(); await h.fulfillment.tick();
  assert.equal(h.provisions(), 1); assert.equal(h.sent.length, 0);
  const row = h.db.prepare('SELECT * FROM fulfillments').get();
  assert.ok(row.credential_cipher); assert.ok(!row.credential_cipher.includes(h.payloads[0].temporary_password));
  assert.equal(row.welcome_error, 'EMAIL_DELIVERY_RETRY');
  h.failures.clear(); await h.fulfillment.tick(); assert.equal(h.sent.length, 0);
  h.advance(); await h.fulfillment.tick();
  assert.equal(h.sent.length, 2); assert.equal(h.provisions(), 1);
  assert.equal(h.fulfillment.publicState(o.id).welcomeEmail, 'sent');
});

test('lost provisioning response retries the same credential and immutable order after restart', async t => {
  const h = harness(t); h.failures.add('lost-response'); const o = h.order(); await h.fulfillment.tick();
  assert.equal(h.sent.length, 1); assert.equal(h.fulfillment.publicState(o.id).accountStatus, 'preparing');
  h.failures.clear(); h.advance();
  const worker = createFulfillment({ db: h.db, env: h.env, fetchImpl: h.fetchImpl, mailer: h.mailer, clock: () => Date.now() + 3600001 });
  await worker.tick();
  assert.equal(h.payloads[0].temporary_password, h.payloads[1].temporary_password);
  assert.equal(h.payloads[0].order_id, h.payloads[1].order_id); assert.equal(h.sent.length, 2);
});

test('existing account invitation never sends a replacement password', async t => {
  const h = harness(t); h.existing(); h.order(); await h.fulfillment.tick();
  assert.ok(h.sent[1].text.includes('existing AisleMint password'));
  assert.ok(h.sent[1].html.includes('Set a new password by email'));
  assert.ok(!h.sent[1].text.includes(h.payloads[0].temporary_password));
});

test('conflicting account requires review while receipt still delivers', async t => {
  const h = harness(t); h.failures.add('review'); const o = h.order(); await h.fulfillment.tick();
  assert.equal(h.sent.length, 1); assert.equal(h.fulfillment.publicState(o.id).accountStatus, 'review');
  h.failures.clear(); h.advance(); await h.fulfillment.tick(); assert.equal(h.sent.length, 1);
});

test('concurrent workers respect the durable per-order lease', async t => {
  const h = harness(t); h.order();
  const second = createFulfillment({ db: h.db, env: h.env, fetchImpl: h.fetchImpl, mailer: h.mailer });
  await Promise.all([h.fulfillment.tick(), second.tick(), h.fulfillment.tick()]);
  assert.equal(h.sent.length, 2); assert.equal(h.provisions(), 1);
});

test('expired temporary invitations stop sending and require owner review', async t => {
  const h = harness(t); h.failures.add('welcome'); const o = h.order(); await h.fulfillment.tick();
  h.failures.clear(); h.advance(73 * 3600000); await h.fulfillment.tick();
  assert.equal(h.fulfillment.publicState(o.id).accountStatus, 'review'); assert.equal(h.sent.length, 1);
  assert.equal(h.db.prepare('SELECT welcome_error FROM fulfillments').get().welcome_error, 'INVITATION_EXPIRED');
});

test('checkout fails closed without encrypted-credential and SMTP configuration', async t => {
  for (const key of ['BILLING_CREDENTIAL_KEY', 'BILLING_PROVISIONING_SECRET', 'SMTP_PASSWORD']) {
    const h = harness(t, { [key]: '' }); assert.equal(h.fulfillment.configured, false); assert.equal(await h.fulfillment.ready(), false);
  }
});
