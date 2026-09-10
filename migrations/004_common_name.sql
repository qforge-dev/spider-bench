-- 004: English common names (iNaturalist preferred_common_name, nullable).
ALTER TABLE taxa ADD COLUMN common_name TEXT;
