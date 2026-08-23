#!/bin/bash
# Watchdog for the 'videx' container.
#
# The VIDEX engine plugin's HTTP client (ask_from_videx_http in ha_videx.cc)
# leaks one unclosed socket fd per stat lookup (~180 per EXPLAIN). The leak is
# in the plugin itself, not the qemu/Rosetta emulation layer, so it cannot be
# avoided without rebuilding the plugin. mysqld crashes with 'Too many open
# files' when its nofile limit is exhausted.
#
# Mitigation: raise mysqld's nofile limit to NOFILE_LIMIT (default 20M, ~20x
# the container default) on every new mysqld pid, which gives ~110k EXPLAINs
# per fd budget — enough for a full benchmark run with no restarts. The
# container restart remains as a backstop if the limit is ever approached.
# The benchmark's MySQL connector auto-reconnects (10 retries over ~275s) and
# re-runs interrupted EXPLAINs, and VIDEX shadow indexes persist on disk,
# so a restart mid-calculation is safe. Restarts are deferred while a real
# (timed) benchmark query is executing to avoid corrupting runtime
# measurements, unless the fd count becomes critical.
#
# Usage: nohup bash scripts/videx_fd_watchdog.sh >> videx_watchdog.log 2>&1 &

# Each leaked fd also leaks ~7.5KB of mysqld heap; the Docker VM (16GB) OOM-kills
# mysqld at ~1.6M fds (~12GB RSS), well before the 20M fd limit. Restart on the
# memory-derived threshold instead.
THRESHOLD=${THRESHOLD:-1200000}
CRITICAL=${CRITICAL:-1450000}
INTERVAL=${INTERVAL:-30}
NOFILE_LIMIT=${NOFILE_LIMIT:-20000000}
NR_OPEN=${NR_OPEN:-20971520}
MYSQL_CLIENT=/root/mysql_server/mysql_build_output/build/runtime_output_directory/mysql

log() { echo "$(date '+%F %T') $*"; }

# fs.nr_open in the Docker VM caps per-process nofile and resets to 1048576
# when Docker Desktop restarts, so re-apply it before each prlimit.
raise_limits() {
  local pid=$1
  docker run --rm --privileged --pid=host alpine \
    nsenter -t 1 -m -- sysctl -w fs.nr_open=$NR_OPEN >/dev/null 2>&1
  docker run --rm --privileged --pid=container:videx alpine sh -c \
    "apk add -q util-linux-misc 2>/dev/null || apk add -q util-linux; \
     prlimit --pid $pid --nofile=$NOFILE_LIMIT:$NOFILE_LIMIT" >/dev/null 2>&1 \
    && log "raised nofile limit to $NOFILE_LIMIT for mysqld pid=$pid" \
    || log "WARNING: failed to raise nofile limit for mysqld pid=$pid"
}

last_pid=""
while true; do
  pid=$(docker exec videx bash -c '
    for p in $(ls /proc | grep -E "^[0-9]+$"); do
      if [ "$(cat /proc/$p/comm 2>/dev/null)" = "mysqld" ]; then echo $p; break; fi
    done' 2>/dev/null)

  if [ -z "$pid" ]; then
    log "mysqld not found (container down or mysqld restarting)"
    last_pid=""
    sleep "$INTERVAL"
    continue
  fi

  # New mysqld process (fresh start or post-restart): raise its fd limit
  if [ "$pid" != "$last_pid" ]; then
    raise_limits "$pid"
    last_pid="$pid"
  fi

  fds=$(docker exec videx bash -c "ls /proc/$pid/fd 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]')
  log "mysqld pid=$pid fds=$fds"

  if [ "${fds:-0}" -ge "$THRESHOLD" ]; then
    # Defer if a real (timed) benchmark query is executing, unless critical.
    # Real queries run on JOB_REFINED; whatif EXPLAINs run on videx_job_refined
    # and are safely retried by the connector after a restart.
    real_q=$(docker exec videx bash -c "$MYSQL_CLIENT -u videx -ppassword -P 13308 -h 127.0.0.1 -N -e \"SELECT COUNT(*) FROM information_schema.processlist WHERE db='JOB_REFINED' AND command='Query' AND info NOT LIKE 'EXPLAIN%' AND info IS NOT NULL;\" 2>/dev/null" 2>/dev/null | tr -d '[:space:]')

    if [ "${real_q:-0}" -gt 0 ] && [ "$fds" -lt "$CRITICAL" ]; then
      log "fds=$fds over threshold but real benchmark query executing; deferring restart"
    else
      log "RESTARTING videx container (fds=$fds, real_queries=${real_q:-0})"
      docker restart videx
      log "restart issued; sleeping 60s for MySQL recovery"
      sleep 60
      last_pid=""
    fi
  fi

  sleep "$INTERVAL"
done
