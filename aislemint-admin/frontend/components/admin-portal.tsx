'use client';
import {
  retail,
  RetailField,
  type Session,
  type Shop,
} from '@/components/retail-platform';
import { ArrowLeft, KeyRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import AccessManager from './access-manager';
import './admin.css';
export default function Management({
  session,
  close,
  refresh,
  select,
}: {
  session: Session;
  close: () => void;
  refresh: () => Promise<void>;
  select: (id: string) => void;
}) {
  const [section, setSection] = useState(
    session.retailers.length ? 'People & roles' : 'Retailers',
  );
  const [retailer, setRetailer] = useState(
      session.retailers.find(
        (r) => session.user.admin || session.user.memberships[r.id] === 'owner',
      )?.id || '',
    ),
    [name, setName] = useState(''),
    [shopName, setShopName] = useState(''),
    [code, setCode] = useState(''),
    [country, setCountry] = useState('IN'),
    [timezone, setTimezone] = useState('Asia/Kolkata'),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(''),
    [token, setToken] = useState(''),
    [keys, setKeys] = useState<any[]>([]);
  const owner =
    session.user.admin || session.user.memberships[retailer] === 'owner';
  const shops = session.retailers.find((r) => r.id === retailer)?.shops || [];
  const existingShop = shops.find((shop) => shop.store_code === code);
  const run = async (fn: () => Promise<void>) => {
    if (busy) return;
    setError('');
    setBusy(true);
    try {
      await fn();
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    setToken('');
    setKeys([]);
    if (retailer && owner)
      retail<any[]>('/retailers/' + retailer + '/keys')
        .then(setKeys)
        .catch((e) => setError(e.message));
  }, [retailer, owner]);
  return (
    <section className="admin-management">
      <header>
        <div>
          <p className="retail-eyebrow">
            {session.user.admin
              ? 'PLATFORM ADMINISTRATION'
              : 'RETAILER MANAGEMENT'}
          </p>
          <h1>Administration</h1>
          <p>Manage your shops, people and integration access.</p>
        </div>
        <button aria-label="Back to platform" onClick={close}>
          <ArrowLeft size={17} /> Back to platform
        </button>
      </header>
      {error && (
        <p className="retail-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="retail-note">
          {notice}
        </p>
      )}
      <nav className="admin-tabs" aria-label="Administration sections">
        {[
          ...(session.user.admin ? ['Retailers'] : []),
          'Shops',
          'People & roles',
          'Integration credentials',
        ].map((item) => (
          <button
            key={item}
            type="button"
            aria-current={section === item ? 'page' : undefined}
            onClick={() => setSection(item)}
          >
            {item}
          </button>
        ))}
      </nav>
      <fieldset disabled={busy}>
        {session.user.admin && section === 'Retailers' && (
          <form
            className="retail-inline"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                const r = await retail('/retailers', { name });
                setRetailer(r.id);
                setSection('Shops');
                setName('');
                setNotice('Retailer created. Add its first shop below.');
              });
            }}
          >
            <RetailField label="New retailer name">
              <input
                required
                minLength={2}
                maxLength={120}
                placeholder="e.g. Evergreen Grocers"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </RetailField>
            <button className="retail-primary">Create retailer</button>
          </form>
        )}
        <RetailField label="Retailer">
          <select
            value={retailer}
            onChange={(e) => setRetailer(e.target.value)}
          >
            <option value="">Select retailer</option>
            {session.retailers
              .filter(
                (r) =>
                  session.user.admin ||
                  session.user.memberships[r.id] === 'owner',
              )
              .map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
          </select>
        </RetailField>
        {retailer && owner && (
          <>
            {section === 'Shops' && (
              <>
                {shops.length > 0 && (
                  <section aria-label="Existing shop workspaces">
                    <h3>Your shop workspaces</h3>
                    {shops.map((shop) => (
                      <div
                        className="retail-inline retail-snapshot"
                        key={shop.id}
                      >
                        <div>
                          <strong>{shop.name}</strong>
                          <p>Store code: {shop.store_code}</p>
                        </div>
                        <button type="button" onClick={() => select(shop.id)}>
                          Open workspace
                        </button>
                      </div>
                    ))}
                  </section>
                )}
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (existingShop || busy) return;
                    void run(async () => {
                      let s: Shop;
                      try {
                        s = await retail<Shop>(
                          '/retailers/' + retailer + '/shops',
                          {
                            name: shopName,
                            store_code: code,
                            country,
                            timezone,
                          },
                        );
                      } catch (error) {
                        // Refresh stale lists after another session creates the same code.
                        await refresh().catch(() => undefined);
                        throw error;
                      }
                      await refresh();
                      select(s.id);
                    });
                  }}
                >
                  <h3>Add a shop</h3>
                  <p>
                    One permanent workspace per shop. Use its store code in
                    every history record.
                  </p>
                  <div className="retail-two">
                    <RetailField label="Shop name">
                      <input
                        required
                        minLength={2}
                        maxLength={120}
                        value={shopName}
                        onChange={(e) => setShopName(e.target.value)}
                      />
                    </RetailField>
                    <RetailField label="Store code">
                      <input
                        required
                        pattern="[A-Za-z0-9_-]{1,64}"
                        placeholder="SHOP-001"
                        value={code}
                        aria-invalid={Boolean(existingShop)}
                        aria-describedby={
                          existingShop ? 'shop-code-conflict' : undefined
                        }
                        onChange={(e) => {
                          setCode(e.target.value);
                          setError('');
                        }}
                      />
                    </RetailField>
                  </div>
                  {existingShop && (
                    <div
                      id="shop-code-conflict"
                      className="retail-note"
                      role="status"
                    >
                      <p>
                        {existingShop.store_code} already belongs to{' '}
                        {existingShop.name}. Open its workspace above, or use a
                        different store code for a new shop.
                      </p>
                    </div>
                  )}
                  <RetailField label="Market">
                    <select
                      value={country}
                      onChange={(e) => {
                        setCountry(e.target.value);
                        setTimezone(
                          e.target.value === 'IN'
                            ? 'Asia/Kolkata'
                            : 'America/New_York',
                        );
                      }}
                    >
                      <option value="IN">India · INR</option>
                      <option value="US">United States · USD</option>
                    </select>
                  </RetailField>
                  <RetailField label="Shop timezone (IANA)">
                    <input
                      required
                      value={timezone}
                      onChange={(e) => setTimezone(e.target.value)}
                    />
                  </RetailField>
                  <button
                    className="retail-primary"
                    disabled={busy || Boolean(existingShop)}
                  >
                    {busy ? 'Creating workspace…' : 'Create shop workspace'}
                  </button>
                </form>
              </>
            )}
            {section === 'People & roles' && (
              <AccessManager key={retailer} retailer={retailer} />
            )}
            {section === 'Integration credentials' && (
              <>
                <details>
                  <summary>Integration credentials</summary>
                  <p>
                    Connect an ERP or data pipeline with a key restricted to
                    this retailer. Keys cannot activate models or access
                    OneSales.
                  </p>
                  <button
                    onClick={() =>
                      void run(async () => {
                        const key = await retail(
                          '/retailers/' + retailer + '/keys',
                          { name: 'Sales history connector' },
                        );
                        setToken(key.token);
                        setKeys(
                          await retail('/retailers/' + retailer + '/keys'),
                        );
                      })
                    }
                  >
                    <KeyRound size={15} />
                    Create ingestion key
                  </button>
                  {token && (
                    <div className="retail-secret">
                      <p>Copy now. The full key is shown only once.</p>
                      <code>{token}</code>
                      <button
                        onClick={() =>
                          void navigator.clipboard.writeText(token)
                        }
                      >
                        Copy key
                      </button>
                    </div>
                  )}
                  {keys.map((k) => (
                    <div className="retail-inline" key={k.id}>
                      <span>
                        {k.name} · {k.id.slice(0, 8)}
                      </span>
                      <button
                        onClick={() =>
                          void run(async () => {
                            await retail(
                              `/retailers/${retailer}/keys/${k.id}/revoke`,
                              {},
                            );
                            setKeys(keys.filter((x) => x.id !== k.id));
                            setToken('');
                          })
                        }
                      >
                        Revoke
                      </button>
                    </div>
                  ))}
                </details>
              </>
            )}
          </>
        )}
        {retailer && !owner && (
          <p>
            Your account can access planning. A retailer owner manages shops and
            accounts.
          </p>
        )}
      </fieldset>
    </section>
  );
}
