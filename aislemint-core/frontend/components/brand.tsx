import { BRAND } from '@/lib/brand';

/** The exact geometric mark used on the AisleMint landing page. */
export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <svg
      className={`am-mark ${className}`}
      viewBox="0 0 36 38"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 30 14 6h8L12 30z" />
      <path d="m19 20 5-12 9 22H23z" />
      <path d="M2 33h31v3H2z" />
    </svg>
  );
}

export function Brand({ className = '' }: { className?: string }) {
  return (
    <span className={`am-brand ${className}`} aria-label={BRAND.name}>
      <BrandMark />
      <span>
        {BRAND.name}
        <span className="am-period" aria-hidden="true">
          .
        </span>
      </span>
    </span>
  );
}
