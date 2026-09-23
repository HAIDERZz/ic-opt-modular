#!/usr/bin/env bash
# Progress of the transformer library build: finished EMX runs per project, live EMX processes and their summed RSS,
# the highest summed RSS the sampler has seen, and the last failures in the run log.
root=${1:-/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28}
total=0
for p in xfm_bs_ap xfm_bs_m10 xfm_ms_ap xfm_ms_m10; do
  d=$root/$p/.icopt/sims
  [ -d "$d" ] || continue
  n=$(grep -l "Wall-clock time" "$d"/obs_*/em/xfm/emx.log 2>/dev/null | wc -l)
  q=$(ls "$d"/obs_*/xfm/nominal/quantities.json 2>/dev/null | wc -l)
  echo "$p: emx_done=$n measured=$q"
  total=$((total + n))
done
echo "TOTAL_DONE=$total"
echo "emx_running=$(pgrep -xc emx) emx_rss_gb=$(ps -C emx -o rss= | awk '{s+=$1} END {printf "%.1f", s/1048576}')"
[ -f "$root/xfm_mem_samples.log" ] && echo "emx_rss_gb_max_seen=$(awk '{if ($3>m) m=$3} END {printf "%.1f", m}' "$root/xfm_mem_samples.log")"
grep -E "not ok|Traceback|Error" "$root/xfm_run.log" | tail -5
