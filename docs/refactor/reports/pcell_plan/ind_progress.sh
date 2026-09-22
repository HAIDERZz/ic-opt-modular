#!/usr/bin/env bash
# Progress of the inductor library build: finished EMX runs (a log with a wall-clock line) per project, failures, live EMX processes.
root=${1:-/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28}
total=0
for p in ind_sym_ap ind_sym_ap_nt1 ind_sym_m10 ind_sym_m10_nt1; do
  d=$root/$p/.icopt/sims
  [ -d "$d" ] || continue
  n=$(grep -l "Wall-clock time" "$d"/obs_*/em/ind/emx.log 2>/dev/null | wc -l)
  q=$(ls "$d"/obs_*/ind/nominal/quantities.json 2>/dev/null | wc -l)
  echo "$p: emx_done=$n measured=$q"
  total=$((total + n))
done
echo "TOTAL_DONE=$total"
echo "emx_running=$(pgrep -xc emx)"
grep -E "not ok|failed|Error" "$root/run.log" | tail -5
