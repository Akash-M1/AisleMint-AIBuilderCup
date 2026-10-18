/** Versioned UI handoff. Persisted campaigns remain the authoritative signal. */
export type DemandHandoff = {
  source_id: string;
  store_id: string;
  product_ids: string[];
  horizon: number;
  nonce: string;
};
export type ProductArea = 'promotions' | 'demand' | 'sales' | 'data' | 'connect';
export type AccessLevel = 'none' | 'viewer' | 'user' | 'editor';
