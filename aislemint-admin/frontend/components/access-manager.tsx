'use client';
import { retail, RetailField } from '@/components/retail-platform';
import type { AccessLevel, ProductArea } from '@/lib/workspace-contracts';
import { RefreshCw, Search, ShieldCheck, UserPlus } from 'lucide-react';
import { useEffect, useState } from 'react';
const areas: [ProductArea, string][] = [
  ['promotions', 'Promotions'],
  ['demand', 'Demand & inventory'],
  ['sales', 'Sales & analytics'],
  ['data', 'Data & data science'],
  ['connect', 'Connected intelligence'],
];
const levels: AccessLevel[] = ['none', 'viewer', 'user', 'editor'];
const emptyRoles = () =>
  Object.fromEntries(areas.map(([id]) => [id, 'none'])) as Record<
    ProductArea,
    AccessLevel
  >;
type Account = {
  id: string;
  name: string;
  email: string;
  membership: string;
  roles: Record<ProductArea, AccessLevel>;
  revision: number;
  disabled: boolean;
};
export default function AccessManager({ retailer }: { retailer: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]),
    [audit, setAudit] = useState<any[]>([]),
    [selected, setSelected] = useState<Account | null>(null);
  const [creating, setCreating] = useState(false),
    [search, setSearch] = useState(''),
    [name, setName] = useState(''),
    [email, setEmail] = useState(''),
    [password, setPassword] = useState('');
  const [membership, setMembership] = useState('member'),
    [roles, setRoles] = useState(emptyRoles),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const path = '/retailers/' + retailer;
  const refresh = async () => {
    const [a, b] = await Promise.all([
      retail<Account[]>(path + '/accounts'),
      retail<any[]>(path + '/access-audit'),
    ]);
    setAccounts(a);
    setAudit(b);
  };
  useEffect(() => {
    let current = true;
    Promise.all([
      retail<Account[]>(path + '/accounts'),
      retail<any[]>(path + '/access-audit'),
    ])
      .then(([a, b]) => {
        if (current) {
          setAccounts(a);
          setAudit(b);
        }
      })
      .catch((e) => {
        if (current) setError(e.message);
      });
    return () => {
      current = false;
    };
  }, [path]);
  function edit(account: Account | null) {
    setSelected(account);
    setCreating(!account);
    setRoles(account?.roles || emptyRoles());
    setMembership(account?.membership === 'owner' ? 'owner' : 'member');
    setName('');
    setEmail('');
    setPassword('');
    setError('');
    setNotice('');
  }
  async function save(remove = false) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      if (selected)
        await retail(`${path}/accounts/${selected.id}/access`, {
          revision: selected.revision,
          membership: remove ? null : membership,
          roles,
        });
      else
        await retail(path + '/accounts', {
          name,
          email,
          password,
          membership,
          roles,
        });
      setPassword('');
      setCreating(false);
      setSelected(null);
      await refresh();
      setNotice(
        remove
          ? 'Retailer access removed. Existing sessions were revoked.'
          : selected
            ? 'Roles updated. This person must sign in again.'
            : 'Account created. Share credentials through your secure channel; no email was sent.',
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="iam-panel" aria-labelledby="iam-title">
      <div className="iam-heading">
        <div>
          <p className="retail-eyebrow">IDENTITY & ACCESS</p>
          <h2 id="iam-title">The right tools for every person.</h2>
          <p>
            Roles apply to all shops in this retailer. Each person has their own
            account.
          </p>
        </div>
        <button
          type="button"
          className="retail-primary"
          onClick={() => edit(null)}
        >
          <UserPlus size={16} /> Add account
        </button>
      </div>
      {error && (
        <p className="retail-error" role="alert">
          {error}{' '}
          <button
            type="button"
            onClick={() => void refresh().catch((e) => setError(e.message))}
          >
            Refresh accounts
          </button>
        </p>
      )}
      {notice && (
        <p className="retail-note" role="status">
          {notice}
        </p>
      )}
      <div className="iam-search">
        <Search size={17} />
        <input
          aria-label="Search accounts"
          placeholder="Search by name or email"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button
          type="button"
          aria-label="Refresh accounts"
          onClick={() => void refresh().catch((e) => setError(e.message))}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      <div className="iam-accounts">
        {accounts
          .filter((a) =>
            (a.name + ' ' + a.email)
              .toLowerCase()
              .includes(search.toLowerCase()),
          )
          .map((a) => (
            <button
              type="button"
              className={
                'iam-account ' + (a.id === selected?.id ? 'selected' : '')
              }
              key={a.id}
              onClick={() => edit(a)}
            >
              <span className="iam-avatar">
                {a.name.slice(0, 2).toUpperCase()}
              </span>
              <span>
                <strong>{a.name}</strong>
                <small>{a.email}</small>
              </span>
              <span className="iam-tag">
                {a.membership === 'owner'
                  ? 'Owner'
                  : Object.values(a.roles).filter((x) => x !== 'none').length +
                    ' areas'}
              </span>
            </button>
          ))}
        {!accounts.length && (
          <p>No retailer accounts yet. Add an owner to complete onboarding.</p>
        )}
      </div>
      {(creating || selected) && (
        <form
          className="iam-editor"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset disabled={busy}>
            <h3>
              {selected
                ? 'Access for ' + selected.name
                : 'Create a named account'}
            </h3>
            {!selected && (
              <>
                <div className="retail-two">
                  <RetailField label="Full name">
                    <input
                      required
                      minLength={2}
                      maxLength={120}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </RetailField>
                  <RetailField label="Email">
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </RetailField>
                </div>
                <RetailField label="Initial password · 12–128 characters">
                  <input
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={12}
                    maxLength={128}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </RetailField>
              </>
            )}
            <RetailField label="Retailer responsibility">
              <select
                value={membership}
                onChange={(e) => setMembership(e.target.value)}
              >
                <option value="member">
                  Team member · assigned product roles
                </option>
                <option value="owner">
                  Retailer owner · full retailer administration
                </option>
              </select>
            </RetailField>
            {membership === 'owner' ? (
              <p className="retail-note">
                <ShieldCheck size={18} /> Owners can use all areas, manage
                accounts and approve protected actions. An active retailer must
                retain at least one owner.
              </p>
            ) : (
              <div className="iam-matrix">
                {areas.map(([id, label]) => (
                  <RetailField key={id} label={label}>
                    <select
                      aria-label={label + ' role'}
                      value={roles[id]}
                      onChange={(e) =>
                        setRoles({
                          ...roles,
                          [id]: e.target.value as AccessLevel,
                        })
                      }
                    >
                      {levels.map((level) => (
                        <option key={level} value={level}>
                          {level === 'none'
                            ? 'No access'
                            : label +
                              ' ' +
                              level[0].toUpperCase() +
                              level.slice(1)}
                        </option>
                      ))}
                    </select>
                  </RetailField>
                ))}
              </div>
            )}
            <div className="iam-role-guide">
              <p>
                <strong>Viewer</strong> reads existing results and records.
              </p>
              <p>
                <strong>User</strong> also runs analysis and forecasts; Sales
                Users can operate checkout and returns with a staff PIN.
              </p>
              <p>
                <strong>Editor</strong> also saves plans, confirms promotions,
                trains models and manages operational settings. Owner approval
                still protects model activation and sensitive connections.
              </p>
            </div>
            <div className="iam-actions">
              <button className="retail-primary">
                {busy ? 'Saving…' : selected ? 'Save access' : 'Create account'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setCreating(false);
                  setSelected(null);
                  setPassword('');
                }}
              >
                Cancel
              </button>
              {selected && (
                <button
                  type="button"
                  className="iam-danger"
                  onClick={() => {
                    if (
                      window.confirm(
                        'Remove ' +
                          selected.name +
                          ' from this retailer and revoke their sessions?',
                      )
                    )
                      void save(true);
                  }}
                >
                  Remove retailer access
                </button>
              )}
            </div>
          </fieldset>
        </form>
      )}
      <details>
        <summary>Access activity · latest 100 changes</summary>
        <div className="iam-audit">
          {audit.map((a) => (
            <div key={a.id}>
              <strong>{a.event.replaceAll('_', ' ')}</strong>
              <span>{new Date(a.created).toLocaleString()}</span>
              <small>
                Actor {a.actor} · Account {a.details?.user_id}
              </small>
            </div>
          ))}
          {!audit.length && <p>Access changes will appear here.</p>}
        </div>
      </details>
    </section>
  );
}
