# T0 Spike: SINPA Dataset Feasibility (Approach C)

Verdict: GO

All three exit criteria pass. Caveat that shapes how we use it: the data is
COVID-era (Jul 2020 - Jun 2021), so it is a pretraining/bootstrap corpus, not
a substitute for live 2026 polling. Two of the ten seed carparks (Raffles
City, VivoCity P2) are absent and remain on the Approach A timeline.

## Exit criterion 1 - Format: YES

The dataset is freely downloadable, no request/gating, from HuggingFace
(Huaiwu/SINPA): data/train.npz (5.93 GB), data/val.npz (593 MB),
data/test.npz (594 MB). Verified by anonymously downloading val.npz in full
and loading it: `x` has shape (1217, 12, 1687, 12) and `y` has shape
(1217, 12, 1687, 1) -- pre-windowed samples of 12 input timesteps x 1687
carparks x 12 features, with a 12-step forecast target. Granularity is 15
minutes (resampled from a 5-minute crawl), well inside our 30-minute bar, and
dim 0 is raw available-lot counts (verified values 0-4038, mean 183, 0% NaN).
Span is one year, 2020-07-01 to 2021-06-30, chronological 10:1:1
train/val/test split; absolute timestamps are recoverable from the
time-of-day/weekday/holiday feature dims plus the known span. Note the
release is windowed samples rather than one continuous series, so T5 needs a
small de-windowing step.

## Exit criterion 2 - Licence: YES

The SINPA README states: "The SINPA dataset is released under the Singapore
Open Data Licence" (SODL 1.0, data.gov.sg/open-data-licence). SODL grants a
worldwide, royalty-free, non-exclusive right to "use, access, download, copy,
distribute, transmit, modify and adapt the datasets, or any derived analyses
or applications, whether commercially or non-commercially" -- training a
model that serves a free public consumer app is squarely permitted.
Conditions: a conspicuous attribution notice with a link to the licence
(template provided in the licence), and no implying government endorsement.
The repo's code has no licence file, but we only need the data, not DeepPA
code.

## Exit criterion 3 - ID mapping: YES (8 of 10 seeds)

SINPA carries no carpark IDs at all -- lots are identified only by array
index plus a Latitude,Longitude row in aux_data/lots_location.csv (1687
rows). However, the crawl was NOT HDB-only as feared: cross-matching all
1687 coordinates against the current HDB carpark universe (data.gov.sg HDB
Carpark Information, SVY21 converted to WGS84) shows ~89% are exact HDB
matches, and the remainder match the LTA/URA feeds. Pulling the live LTA
DataMall CarParkAvailabilityv2 feed (2603 records: 2410 HDB, 152 URA, 41
LTA) and joining on coordinates gives EXACT 0.0 m matches for 8 of our 10
seed carparks, with the second-nearest SINPA point 77-289 m away, so the
join is unambiguous:

| LTA ID | Development    | SINPA index | Match dist |
|--------|----------------|-------------|------------|
| 1      | Suntec City    | 1584        | 0.0 m      |
| 2      | Marina Square  | 1593        | 0.0 m      |
| 3      | Raffles City   | ABSENT      | (311 m)    |
| 16     | VivoCity P3    | 1590        | 0.0 m      |
| 50     | VivoCity P2    | ABSENT      | (110 m)    |
| 13     | Ngee Ann City  | 1587        | 0.0 m      |
| 24     | 313@Somerset   | 1597        | 0.0 m      |
| 21     | Centrepoint    | 1595        | 0.0 m      |
| 15     | Wheelock Place | 1589        | 0.0 m      |
| 11     | Cineleisure    | 1585        | 0.0 m      |

Raffles City and VivoCity P2 were evidently dropped by SINPA's own filters
(missing rate < 30%, KL-divergence stationarity screen), so no amount of
mapping recovers them. 8/10 meets the "at least most" bar.

## Secondary assessment: data age and distribution shift

This is the big caveat. The retained year (Jul 2020 - Jun 2021) is peak-COVID
Singapore: default work-from-home, closed borders/no tourism, and mall
capacity limits. Orchard/Marina mall parking demand in that window is
suppressed and time-shape-distorted relative to 2026. The authors themselves
discarded the other two crawled years for temporal distribution shift, which
tells you the regime-sensitivity is real. Consequences: (a) do not train a
2026-serving model on SINPA alone; (b) use it to pretrain / warm-start
LightGBM features (lag structure, time-of-day and weekday shapes, weather
interactions) and to de-risk the T5 pipeline end to end, then fine-tune or
re-train on live 2026 polls as they accumulate; (c) validate exclusively on
live 2026 data, never on SINPA held-out data.

## Consequences for the plan

GO changes T5's training data loading as follows: add a one-time SINPA
ingestion step -- download the three NPZ files from HuggingFace, slice the 8
mapped lot indices (table above), de-window the overlapping samples back
into a continuous 15-minute series per carpark, and normalize into our
(carpark_id, ts_utc, available_lots) schema with a source flag
(sinpa_2020 vs live_2026). Train the first model on SINPA slices for the 8
carparks, fine-tune/re-train as live polls accumulate; Raffles City (3) and
VivoCity P2 (50) stay on the Approach A live-only timeline. Live polling
still starts on day one -- SINPA shortens the cold-start, it does not
replace Approach A. Add the SODL attribution notice to the app/docs.

## Sources

- https://github.com/yoshall/SINPA (README: dataset description, 15-min
  resampling, 2020-07-01 to 2021-06-30 span, 1687 lots, licence statement)
- https://huggingface.co/datasets/Huaiwu/SINPA (dataset host; files at
  data/{train,val,test}.npz; val.npz downloaded and inspected 2026-07-04)
- https://raw.githubusercontent.com/yoshall/SINPA/main/aux_data/lots_location.csv
  (1687 Latitude,Longitude rows; no IDs)
- https://arxiv.org/abs/2405.18910 (paper: "Predicting Parking Availability
  in Singapore with Cross-Domain Data")
- https://www.ijcai.org/proceedings/2024/836 (IJCAI 2024 publication)
- https://data.gov.sg/open-data-licence (Singapore Open Data Licence 1.0)
- https://data.gov.sg/api/action/datastore_search?resource_id=d_23f946fa557947f93a8043bbef41dd09
  (HDB Carpark Information, used for HDB-universe cross-match)
- https://datamall2.mytransport.sg/ltaodataservice/CarParkAvailabilityv2
  (live LTA feed pulled 2026-07-04 for authoritative seed coordinates)
