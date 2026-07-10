// Static, client-side seed list of the 10 supported carparks. Powers search
// (no network involved) and doubles as the share-link whitelist (design
// doc Security section: "the frontend validates the carpark_id from a
// share-link URL against the same whitelist client-side"). Must match
// db/schema.sql's `carparks` seed rows exactly -- do not add/remove/rename
// entries here without also updating that schema (server-side canonical
// copy) and vice versa.

export interface SeedCarpark {
  id: string;
  name: string;
  displayName: string;
  postalCode?: string;
  latitude?: number;
  longitude?: number;
}

export const SEED_CARPARKS: readonly SeedCarpark[] = [
  { id: "1", name: "Suntec City", displayName: "Suntec City" },
  { id: "2", name: "Marina Square", displayName: "Marina Square" },
  { id: "3", name: "Raffles City", displayName: "Raffles City" },
  { id: "11", name: "Cineleisure", displayName: "Cineleisure" },
  { id: "13", name: "Ngee Ann City", displayName: "Ngee Ann City" },
  { id: "15", name: "Wheelock Place", displayName: "Wheelock Place" },
  { id: "16", name: "VivoCity P3", displayName: "VivoCity P3" },
  { id: "21", name: "Centrepoint", displayName: "Centrepoint" },
  { id: "24", name: "313@Somerset", displayName: "313@Somerset" },
  { id: "50", name: "VivoCity P2", displayName: "VivoCity P2" },
  { id: "100", name: "Test Mall Beta", displayName: "Test Mall Beta" },
  { id: "205", name: "Test Mall Alpha", displayName: "Test Mall Alpha" },
];

const SEED_CARPARK_IDS: ReadonlySet<string> = new Set(SEED_CARPARKS.map((c) => c.id));

/** Client-side whitelist check -- used for share-link validation (Security). */
export function isKnownCarparkId(id: string): boolean {
  return SEED_CARPARK_IDS.has(id);
}

export function getSeedCarparkById(id: string): SeedCarpark | undefined {
  return SEED_CARPARKS.find((c) => c.id === id);
}

/** Live-filter-as-you-type search against the static list (Design Details). */
export function searchSeedCarparks(query: string): SeedCarpark[] {
  const normalized = query.trim().toLowerCase();
  if (normalized === "") return [];
  return SEED_CARPARKS.filter((c) => c.name.toLowerCase().includes(normalized));
}
