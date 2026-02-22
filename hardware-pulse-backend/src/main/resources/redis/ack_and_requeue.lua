-- KEYS[1] = spider:tasks:inflight (ZSET)
-- KEYS[2] = spider:tasks (ZSET)
-- KEYS[3] = spider:tasks:last_score (HASH)
-- ARGV[1] = keyword
-- ARGV[2] = delta
-- Returns: 1

local keyword = ARGV[1]
local delta = tonumber(ARGV[2]) or 0

-- remove from inflight (ACK)
redis.call('ZREM', KEYS[1], keyword)

-- restore previous score if exists
local oldScoreStr = redis.call('HGET', KEYS[3], keyword)
local oldScore = tonumber(oldScoreStr) or 0

local newScore = oldScore + delta
redis.call('ZADD', KEYS[2], newScore, keyword)

-- keep last_score in sync for next round
redis.call('HSET', KEYS[3], keyword, newScore)

return 1
