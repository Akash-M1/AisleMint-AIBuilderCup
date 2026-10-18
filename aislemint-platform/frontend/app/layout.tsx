import type { Metadata, Viewport } from 'next';
import { BRAND } from '@/lib/brand';
import './globals.css';
import PlatformApp from '@/components/platform-app';
export const metadata: Metadata = {
  title: 'AisleMint | Retail Intelligence',
  applicationName: BRAND.name,
  description: BRAND.description,
  icons: { icon: '/brand/aislemint-icon.svg' },
};
export const viewport: Viewport = { themeColor: '#173e32' };
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-IN">
      <body><PlatformApp />{children}</body>
    </html>
  );
}
