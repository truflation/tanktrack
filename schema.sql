-- hormuz-monitor schema (SQLite)
-- All timestamps are ISO-8601 UTC strings for easy sorting + human readability.
-- Booleans stored as INTEGER (0/1).

PRAGMA foreign_keys = ON;

-- Per-vessel static metadata
CREATE TABLE IF NOT EXISTS vessels (
    mmsi            INTEGER PRIMARY KEY,
    name            TEXT,
    vessel_type     INTEGER,        -- AIS ship-type code
    vessel_type_str TEXT,           -- human label
    imo             INTEGER,
    callsign        TEXT,
    length_m        REAL,
    width_m         REAL,
    draught_m       REAL,
    destination     TEXT,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Latest observed state per vessel
CREATE TABLE IF NOT EXISTS vessel_state (
    mmsi            INTEGER PRIMARY KEY REFERENCES vessels(mmsi) ON DELETE CASCADE,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    sog_kt          REAL,
    cog             REAL,
    inside          INTEGER NOT NULL DEFAULT 0,   -- 0/1
    anchored        INTEGER NOT NULL DEFAULT 0,   -- 0/1
    stopped_since   TEXT,
    last_seen       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS vessel_state_inside_idx ON vessel_state(inside);
CREATE INDEX IF NOT EXISTS vessel_state_anchored_idx ON vessel_state(anchored);
CREATE INDEX IF NOT EXISTS vessel_state_last_seen_idx ON vessel_state(last_seen);

-- Append-only event log of discrete transitions
CREATE TABLE IF NOT EXISTS transitions (
    id              TEXT PRIMARY KEY,
    mmsi            INTEGER NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN ('entered','exited','anchored','resumed')),
    direction       TEXT,           -- 'north'/'south'/'east'/'west' for exits
    lat             REAL,
    lon             REAL,
    sog_kt          REAL,
    at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS transitions_mmsi_at_idx ON transitions(mmsi, at DESC);
CREATE INDEX IF NOT EXISTS transitions_at_idx ON transitions(at DESC);
CREATE INDEX IF NOT EXISTS transitions_event_type_idx ON transitions(event_type);

-- Convenience views
DROP VIEW IF EXISTS current_inside;
CREATE VIEW current_inside AS
SELECT v.mmsi, v.name, v.vessel_type_str, s.lat, s.lon, s.sog_kt,
       s.anchored, s.stopped_since, s.last_seen
FROM vessel_state s
JOIN vessels v ON v.mmsi = s.mmsi
WHERE s.inside = 1;

DROP VIEW IF EXISTS counts_24h;
CREATE VIEW counts_24h AS
SELECT
    SUM(CASE WHEN event_type='entered'  AND at > datetime('now','-24 hours') THEN 1 ELSE 0 END) AS entered_24h,
    SUM(CASE WHEN event_type='exited'   AND at > datetime('now','-24 hours') THEN 1 ELSE 0 END) AS exited_24h,
    SUM(CASE WHEN event_type='anchored' AND at > datetime('now','-24 hours') THEN 1 ELSE 0 END) AS anchored_24h,
    SUM(CASE WHEN event_type='resumed'  AND at > datetime('now','-24 hours') THEN 1 ELSE 0 END) AS resumed_24h
FROM transitions;
