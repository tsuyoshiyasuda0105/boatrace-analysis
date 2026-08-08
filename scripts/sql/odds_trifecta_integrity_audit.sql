-- odds_trifecta の重複・不完全データ監査
-- 非破壊。削除や制約追加は行わない。

-- 1. race_id + snapshot_label + combination 単位の重複件数
SELECT
  race_id,
  COALESCE(snapshot_label, '(null)') AS snapshot_label,
  combination,
  COUNT(*) AS duplicate_rows,
  MIN(recorded_at) AS first_recorded_at,
  MAX(recorded_at) AS last_recorded_at
FROM odds_trifecta
GROUP BY race_id, COALESCE(snapshot_label, '(null)'), combination
HAVING COUNT(*) > 1
ORDER BY duplicate_rows DESC, race_id, snapshot_label, combination;

-- 2. race_id + snapshot_label 単位の組み合わせ件数と欠損数
SELECT
  race_id,
  COALESCE(snapshot_label, '(null)') AS snapshot_label,
  COUNT(DISTINCT combination) AS combination_count,
  120 - COUNT(DISTINCT combination) AS missing_combinations,
  MIN(recorded_at) AS first_recorded_at,
  MAX(recorded_at) AS last_recorded_at
FROM odds_trifecta
GROUP BY race_id, COALESCE(snapshot_label, '(null)')
HAVING COUNT(DISTINCT combination) < 120
ORDER BY missing_combinations DESC, race_id, snapshot_label;

-- 3. final オッズ欠損レース
SELECT
  r.race_id,
  r.race_date,
  r.stadium_number,
  r.race_number,
  COUNT(DISTINCT o.combination) AS final_combination_count,
  120 - COUNT(DISTINCT o.combination) AS final_missing_combinations
FROM races r
LEFT JOIN odds_trifecta o
  ON o.race_id = r.race_id
 AND COALESCE(o.snapshot_label, '') = 'final'
GROUP BY r.race_id, r.race_date, r.stadium_number, r.race_number
HAVING COUNT(DISTINCT o.combination) < 120
ORDER BY r.race_date DESC, r.stadium_number, r.race_number;
