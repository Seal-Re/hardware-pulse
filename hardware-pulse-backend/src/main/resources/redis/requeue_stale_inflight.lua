-- KEYS[1] = spider:tasks:inflight (ZSET)
-- KEYS[2] = spider:tasks (ZSET)
-- KEYS[3] = spider:tasks:last_score (HASH)
-- ARGV[1] = cutoffTs (unix seconds, inclusive)
-- ARGV[2] = penaltyDelta (usually negative)
-- ARGV[3] = limit (max members)
-- Returns: movedCount

local cutoff = tonumber(ARGV[1]) or 0
local penalty = tonumber(ARGV[2]) or 0
local limit = tonumber(ARGV[3]) or 100

local members = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', cutoff, 'LIMIT', 0, limit)
if (not members) or (#members == 0) then
  return 0
end

local moved = 0
for i = 1, #members do
  local kw = members[i]
  redis.call('ZREM', KEYS[1], kw)

  local oldScoreStr = redis.call('HGET', KEYS[3], kw)
  local oldScore = tonumber(oldScoreStr) or 0
  local newScore = oldScore + penalty
  redis.call('ZADD', KEYS[2], newScore, kw)
  redis.call('HSET', KEYS[3], kw, newScore)
  moved = moved + 1
end

return moved
