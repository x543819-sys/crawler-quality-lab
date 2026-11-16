-- MySQL练习：仅针对本项目数据库，不操作其他业务库。
USE crawler_quality_lab;

-- 按模式、来源统计当前唯一业务记录。
SELECT mode, source, COUNT(*) AS record_count
FROM cq_records GROUP BY mode, source;

-- 查最近批次状态。
SELECT id, mode, status, started_at, finished_at
FROM cq_runs ORDER BY started_at DESC LIMIT 10;

-- 找出失败页和诊断信息。
SELECT run_id, source, page, status, error
FROM cq_tasks WHERE status = 'failed';

-- 按批次和来源统计通过记录。
SELECT run_id, source, outcome, COUNT(*) AS record_count
FROM cq_observations GROUP BY run_id, source, outcome;

-- 检查观察记录是否都能关联到业务记录。
SELECT COUNT(*) AS unmatched
FROM cq_observations AS o
LEFT JOIN cq_records AS r ON o.record_key = r.record_key
WHERE r.record_key IS NULL;

-- JSON字段查询：MySQL 8.x。
SELECT JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.title')) AS title,
       JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.price')) AS price
FROM cq_records WHERE source = 'books' AND mode = 'live' LIMIT 5;

-- 安全练习增删改查：独立备注表，不修改采集原始记录。
CREATE TABLE IF NOT EXISTS cq_practice_notes (
    note_id VARCHAR(64) PRIMARY KEY,
    note_text TEXT NOT NULL
);
INSERT INTO cq_practice_notes (note_id, note_text)
VALUES ('learning_note_001', '待核查')
ON DUPLICATE KEY UPDATE note_text = '待核查';
UPDATE cq_practice_notes SET note_text = '已核查'
WHERE note_id = 'learning_note_001';
SELECT * FROM cq_practice_notes WHERE note_id = 'learning_note_001';
DELETE FROM cq_practice_notes WHERE note_id = 'learning_note_001';
