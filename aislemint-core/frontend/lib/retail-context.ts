import { createContext } from 'react';
export type Shop = {
  id: string;
  name: string;
  retailer_id: string;
  store_code: string;
  country: string;
  currency: string;
  pos_store_id?: string;
  demo?: boolean;
  active: Record<string, string>;
  revision: number;
};
export type Session = {
  user: {
    id: string;
    name: string;
    email: string;
    admin: boolean;
    must_change_password?: boolean;
    memberships: Record<string, string>;
  };
  retailers: {
    id: string;
    name: string;
    shops: Shop[];
    roles: Record<string, string>;
    subscription?: { plan_id: string; starts_at: string; expires_at: string; renewal: string };
  }[];
};
export const Context = createContext<{
  shop: Shop | null;
  session: Session;
  refresh: () => Promise<void>;
  select: (id: string) => void;
} | null>(null);
