-- KEYS[1] = spider:tasks (ZSET)
-- KEYS[2] = spider:tasks:inflight (ZSET)
-- KEYS[3] = spider:tasks:last_score (HASH)
-- ARGV[1] = nowTs (unix seconds)
-- Returns: {member, oldScore} or nil

local popped = redis.call('ZPOPMAX', KEYS[1])
if (not popped) or (#popped == 0) then
  return nil
end

local member = popped[1]
local oldScore = popped[2]

-- Remember its previous priority so we can requeue later (ACK path)
redis.call('HSET', KEYS[3], member, oldScore)

-- Mark as inflight with timestamp score
redis.call('ZADD', KEYS[2], ARGV[1], member)

return {member, oldScore}
