import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { createBilling } from '../../aislemint-website/backend/server.mjs';
import { createFulfillment } from '../../aislemint-website/backend/fulfillment.mjs';

const origin = 'http://127.0.0.1:4173', token = 'a'.repeat(32) + '.' + 'b'.repeat(43);
async function harness(t) {
  let now = Date.now(), fail = false;
  const messages = [], calls = [];
  const env = { BILLING_DB_PATH: ':memory:', BILLING_PUBLIC_ORIGIN: origin,
    BILLING_FULFILLMENT_ENABLED: 'true', BILLING_CREDENTIAL_KEY: Buffer.alloc(32, 6).toString('base64'),
    BILLING_PROVISIONING_SECRET: 'recovery-test-private-service-secret-1234', SMTP_USER: 'hello@aislemint.com', SMTP_PASSWORD: 'fake', SMTP_FROM: 'hello@aislemint.com' };
  const mailer = { verify: async () => true, sendMail: async message => {
    if (fail) throw Error('SMTP private failure'); messages.push(message); return { accepted: ['owner@example.test'] };
  } };
  const fetchImpl = async (url, options) => {
    const raw = options.body, input = JSON.parse(raw); calls.push(input);
    assert.equal(options.headers['X-Aislemint-Signature'], createHmac('sha256', env.BILLING_PROVISIONING_SECRET).update(options.headers['X-Aislemint-Timestamp'] + '.' + raw).digest('hex'));
    if (url.endsWith('/recovery/request')) return Response.json({ recovery: input.email === 'owner@example.test' ? { token, email: input.email, expires_at: new Date(now + 30 * 60000).toISOString() } : null });
    if (url.endsWith('/recovery/complete')) return input.token === token ? Response.json({ status: 'password_changed' }) : Response.json({}, { status: 400 });
    throw Error('Unexpected test route');
  };
  const app = createBilling({ env, mailer, fetchImpl, clock: () => now });
  await new Promise(r => app.server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + app.server.address().port;
  const post = (path, body, headers = {}) => fetch(base + '/api/billing/password-reset/' + path, { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
  t.after(async () => { app.server.closeAllConnections(); await new Promise(r => app.server.close(r)); app.db.close(); });
  return { ...app, env, mailer, fetchImpl, messages, calls, post, advance: ms => { now += ms; }, fail: value => { fail = value; }, time: () => now };
}

test('public recovery replies do not reveal account existence or reset tokens', async t => {
  const h = await harness(t);
  const found = await h.post('request', { email: 'owner@example.test' }), missing = await h.post('request', { email: 'unknown@example.test' });
  assert.equal(found.status, 200); assert.equal(missing.status, 200);
  const text = await found.text(); assert.equal(text, await missing.text()); assert.ok(!text.includes(token));
  assert.match(found.headers.get('cache-control'), /no-store/);
  assert.equal(h.messages.length, 0);
  const row = h.db.prepare('SELECT * FROM password_recoveries').get();
  assert.ok(row.token_cipher && !JSON.stringify(row).includes(token));
  assert.match(h.calls[0].requester_hash, /^[a-f0-9]{64}$/);
});

test('reset email is queued, sent from hello with fragment link, then plaintext capability is cleared', async t => {
  const h = await harness(t); await h.post('request', { email: 'owner@example.test' });
  await h.fulfillment.tick(); await h.fulfillment.tick();
  assert.equal(h.messages.length, 1);
  const mail = h.messages[0]; assert.equal(mail.from.address, 'hello@aislemint.com');
  assert.match(mail.text, /reset-password\.html#token=/); assert.ok(mail.text.includes(token));
  assert.doesNotMatch(mail.text, /Initial password:/);
  assert.ok(h.db.prepare('SELECT sent_at FROM password_recoveries').get().sent_at);
  assert.equal(h.db.prepare('SELECT token_cipher FROM password_recoveries').get().token_cipher, null);
});

test('queued reset mail retries after restart and expired jobs never send', async t => {
  const h = await harness(t); h.fail(true); await h.post('request', { email: 'owner@example.test' }); await h.fulfillment.tick();
  assert.equal(h.messages.length, 0); assert.ok(h.db.prepare('SELECT token_cipher FROM password_recoveries').get().token_cipher);
  h.fail(false); h.advance(61000);
  const restarted = createFulfillment({ db: h.db, env: h.env, mailer: h.mailer, fetchImpl: h.fetchImpl, clock: h.time });
  await restarted.tick(); assert.equal(h.messages.length, 1);
  await h.post('request', { email: 'owner@example.test' }); h.advance(31 * 60000); await restarted.tick();
  assert.equal(h.messages.length, 1); assert.equal(h.db.prepare('SELECT count(*) n FROM password_recoveries').get().n, 0);
});

test('recovery validates origin, token and password, rate limits requests and exposes only safe results', async t => {
  const h = await harness(t);
  assert.equal((await h.post('request', { email: 'owner@example.test' }, { Origin: 'https://other.test' })).status, 403);
  assert.equal((await h.post('complete', { token: 'bad', password: 'short' })).status, 422);
  const good = await h.post('complete', { token, password: 'A-brand-new-password-123' }); assert.equal(good.status, 200); assert.ok(!(await good.text()).includes(token));
  const bad = await h.post('complete', { token: token.slice(0, -1) + 'z', password: 'A-brand-new-password-123' });
  assert.equal(bad.status, 400); assert.doesNotMatch(await bad.text(), /brand-new|bbbbbb/);
  for (let i = 0; i < 8; i++) await h.post('request', { email: 'unknown@example.test' });
  assert.equal((await h.post('request', { email: 'unknown@example.test' })).status, 429);
});
