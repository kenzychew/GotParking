// The 10 T1-validated seed carparks. Mirrors CANDIDATE_CARPARK_IDS in
// scripts/poll_lta_carparks.py and the db/schema.sql seed rows; LTA
// CarParkIDs are strings.

export const SEED_CARPARK_NAMES: Readonly<Record<string, string>> = {
  "1": "Suntec City",
  "2": "Marina Square",
  "3": "Raffles City",
  "11": "Cineleisure",
  "13": "Ngee Ann City",
  "15": "Wheelock Place",
  "16": "VivoCity P3",
  "21": "Centrepoint",
  "24": "313@Somerset",
  "50": "VivoCity P2",
};

export const SEED_CARPARK_ID_LIST: readonly string[] = Object.keys(SEED_CARPARK_NAMES);

export const SEED_CARPARK_IDS: ReadonlySet<string> = new Set(SEED_CARPARK_ID_LIST);
