#!/usr/bin/env bash
# Memory watchdog — checks every 60s, kills laq python if available < THRESHOLD_GB
THRESHOLD_GB=40
LOG=/mnt/laq/RECAST/runs/mem_watchdog.log
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "Watchdog started (threshold: available < ${THRESHOLD_GB}GB)"

while true; do
    AVAIL_KB=$(grep '^MemAvailable' /proc/meminfo | awk '{print $2}')
    AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
    USED_KB=$(grep '^MemTotal' /proc/meminfo | awk '{print $2}')
    TOTAL_GB=$((USED_KB / 1024 / 1024))
    USED_GB=$((TOTAL_GB - AVAIL_GB))

    # List our python processes sorted by RSS
    OUR_PROCS=$(ps aux --sort=-%mem | awk '/laq.*python/{printf "%s %.1fGB %s\n", $2, $6/1024/1024, substr($0,index($0,$11),60)}' | head -5)

    log "MEM: ${USED_GB}/${TOTAL_GB}GB used, ${AVAIL_GB}GB available"
    if [ -n "$OUR_PROCS" ]; then
        log "OUR PROCS:"
        echo "$OUR_PROCS" | while read line; do log "  $line"; done
    fi

    if [ "$AVAIL_GB" -lt "$THRESHOLD_GB" ]; then
        log "WARNING: available < ${THRESHOLD_GB}GB — killing largest laq python process"
        KILL_PID=$(ps aux --sort=-%mem | awk '/laq.*python/{print $2; exit}')
        if [ -n "$KILL_PID" ]; then
            log "KILLING PID $KILL_PID"
            kill -9 "$KILL_PID" && log "Killed $KILL_PID" || log "Kill failed"
        fi
    fi

    sleep 60
done
