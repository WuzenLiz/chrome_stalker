# 🧠 tBot + Stalker – Technical TODO

Internal improvement roadmap for stability, reliability, and production-hardening.

---

# 🔴 CRITICAL (Stability & Data Safety)

## [ ] 1. Decouple Redis Worker and Telegram Sender (tBot)

**Problem:**  
Redis worker currently calls `send_photo()` directly → HTTP blocking can freeze message consumption.

**Solution:**  
- Introduce `send_queue = Queue(maxsize=N)`
- Redis worker → `send_queue.put(path)`
- Dedicated sender thread → consume queue → call `send_photo()`

**Goal:**  
Prevent Redis loop from blocking due to Telegram latency.

---

## [ ] 2. Add Internal Watchdog (Both Agents)

### tBot
- Monitor Redis worker thread.
- If `thread.is_alive() == False` → `os.execv()` self-restart.

### Stalker
- Monitor keyboard thread.
- Restart if dead.

**Goal:**  
Avoid silent failure where process lives but core thread is dead.

---

## [ ] 3. Replace Redis PubSub with Durable Delivery

**Current:** Redis PubSub (fire-and-forget)  
**Risk:** Message loss during reconnect/restart.

**Upgrade Options:**
- Redis Streams (`XADD`, `XREADGROUP`)
- OR Redis List (`LPUSH` + `BRPOP`)

**Goal:** Zero image loss.

---

## [ ] 4. Log Publish Backoff Skips (Stalker)
When skipping publish due to backoff, log explicitly:
``` python
    log.warning("Skipping publish due to backoff window")
```

**Goal:** No silent image drop.

---

## [ ] 5. Proper Redis Cleanup on Reload (Stalker)

Before replacing RedisPublisher instance:
- Call `reset_connection()`

**Goal:** Avoid lingering sockets.

---

# 🟡 IMPROVEMENT (Operational Cleanliness)

## [ ] 6. Add Version Endpoint

Add `/version` to both services:
- git commit hash
- build time
- config version

**Goal:** Know exactly what is running after redeploy.

---

## [ ] 7. Add Metrics Counters

Expose via `/health`:

- total_captures
- total_publish_fail
- total_redis_reconnect
- total_telegram_fail
- queue_size

**Goal:** Debug without reading logs.

---

## [ ] 8. Introduce Bounded Sender Queue

Use:

```python
Queue(maxsize=100)
```

Define overflow policy:

Drop oldest

OR drop newest

OR log and reject

Goal: Prevent unbounded memory growth if Telegram is down.

--- 
## [ ] 9. Separate Control Plane from tBot

Create optional master_service.py:

Responsibilities:

git pull

service restart

health checks

version sync

tBot becomes command interface only.

Goal: Cleaner architecture.

# 🟢 NICE TO HAVE (Production Polish)
## [ ] 10. Idempotency Protection for Image Send

Add hash-based deduplication:

Prevent duplicate sends after restart.

## [ ] 11. Graceful Shutdown Handling

Add:

signal handlers

proper thread join

flush queues before exit

## [ ] 12. Structured Logging (Optional)

Move to JSON logging format:

Better log parsing

Future log aggregation ready

# 🎯 Suggested Execution Order

Decouple sender from Redis worker

Add internal watchdog

Log publish backoff

Add metrics counters

Migrate PubSub → Streams (when ready)