#!/bin/sh
# Full pipeline from scratch: ./run_all.sh <out_dir>
set -e
export PYTHONHASHSEED=0   # generate_v30 seeds per-run randomness with hash(); fix it for reproducible output
OUT="$1"
for s in generate_v30 finalize_v30 patch_v31 reconcile_v31; do echo "== $s"; python3 $s.py "$OUT" > "$OUT.$s.log" 2>&1 || { tail -20 "$OUT.$s.log"; exit 1; }; done
# v3/v4 scripts expect their own files beside the data
cp patch_v3.py patch_v3_text.py patch_v3_severity.py patch_v5_noise.py patch_v5_text.py finalize_v3.py patch_v4_loss.py patch_v4_terms.py slim_v3.py pcb_config.py "$OUT/" 2>/dev/null || true
for s in patch_v3 patch_v3_text patch_v3_severity patch_v5_noise patch_v5_text finalize_v3 patch_v4_loss patch_v4_terms; do echo "== $s"; python3 $s.py "$OUT" > "$OUT.$s.log" 2>&1 || { tail -20 "$OUT.$s.log"; exit 1; }; done
echo "== slim"; python3 slim_v3.py "$OUT" > "$OUT.slim.log" 2>&1 || { tail -20 "$OUT.slim.log"; exit 1; }
echo done
