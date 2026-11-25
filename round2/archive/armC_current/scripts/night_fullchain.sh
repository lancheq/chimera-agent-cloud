#!/bin/bash
# night_fullchain.sh — 0829 夜间全链：T1 全量91 + 自动eval + T1 GEval + 汇总
# 设计要点：
#   - pids 显式枚举 91 个 GT case（agent_input 有 197 case，只跑有 GT 的 91）
#   - 等待用 cmdline 匹配（PID 复用教训，禁 kill -0）
#   - 反泄漏护栏：日志 grep "Predictor loaded" 应 0 命中
#   - GEval 前停 35B（显存互斥），GEval 后 gemma keep_alive:0 卸载
cd /root/shared-nvme/chimera-agent-baseline
OUT=test/output.t1_full_v2_20260829
log(){ echo "[$(date '+%H:%M:%S')] $1" >> /tmp/night_fullchain.log; }

log "Phase 0 · 清残留 + 生成 91 pid 清单"
pkill -f "trace_monitor.py" 2>/dev/null
pkill -f "llama_cpp[.]server" 2>/dev/null
sleep 3
.venv/bin/python - << 'PYEOF'
import json, os
GT = "data/task1/agent_input"
pids = [c for c in sorted(os.listdir(GT))
        if os.path.exists(f"{GT}/{c}/prostate-biopsy-decision.json")]
json.dump(pids, open("scripts/t1_full_91_gt_pids.json", "w"))
print(f"pid count = {len(pids)}")
PYEOF
N_PIDS=$(.venv/bin/python -c "import json;print(len(json.load(open('scripts/t1_full_91_gt_pids.json'))))")
log "pid 清单: $N_PIDS 个（非 91 则中止）"
[ "$N_PIDS" = "91" ] || { log "ABORT: pid 数 $N_PIDS != 91"; exit 1; }

log "Phase 1 · 启动 35B"
nohup .venv/bin/python -m llama_cpp.server \
  --model model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8765 --n_gpu_layers -1 --n_ctx 16384 \
  > qwen_restart_night.log 2>&1 < /dev/null &
READY=0
for i in $(seq 1 60); do
  sleep 10
  curl -s --max-time 5 http://127.0.0.1:8765/v1/models | grep -q "Qwen" && { READY=1; break; }
done
[ "$READY" = "1" ] || { log "ABORT: 35B 10min 未就绪"; exit 1; }
log "35B ready (GPU $(nvidia-smi --query-gpu=memory.used --format=csv,noheader))"

log "Phase 2 · trace_monitor --watch + T1 全量 91（双关闭）"
nohup .venv/bin/python scripts/trace_monitor.py --pred $OUT --watch \
  > /tmp/trace_monitor_t1full.log 2>&1 < /dev/null &
PIDS=$(.venv/bin/python -c "import json;print(','.join(json.load(open('scripts/t1_full_91_gt_pids.json'))))")
nohup .venv/bin/python -m chimera_agent_baseline.run \
  +experiment=qwen_local \
  agent.tasks=[1] \
  agent.pids="[$PIDS]" \
  agent.predictor.enabled=false \
  agent.predictor.decision_override_tasks=[] \
  agent.form_fill.self_refine=false \
  agent.form_fill.atomic_check=false \
  paths.output_dir=$OUT > t1_full_run.log 2>&1 < /dev/null &
log "T1 full launched，等待完成（cmdline 匹配轮询 60s）"

while ps aux | grep "paths.output_dir=$OUT" | grep -v grep > /dev/null; do
  sleep 60
done
DONE_N=$(grep -c "done in" t1_full_run.log)
FAIL_N=$(grep -c "failed" t1_full_run.log || true)
log "T1 full 完成: $DONE_N done / $FAIL_N failed"

log "Phase 3 · 反泄漏护栏 + 官方 eval"
LEAK=$(grep -c "Predictor loaded" t1_full_run.log || true)
log "反泄漏: 'Predictor loaded' 命中 = $LEAK（应为 0）"
grep -m2 "predictor=None" t1_full_run.log > /dev/null && log "护栏: form_fill predictor=None 确认"
.venv/bin/python scripts/eval_train_gt.py \
  --pred-dir $OUT --report-suffix _t1_full_v2_20260829 > /dev/null 2>&1
log "eval 完成: scripts/eval_train_gt_report_t1_full_v2_20260829.md"

log "Phase 4 · vs 0827 全量对比"
.venv/bin/python - << 'PYEOF' > /tmp/t1_full_compare.txt 2>&1
import json, os
GT="data/task1/agent_input"; OLD="test/output.final_20260826/task1"; NEW="test/output.t1_full_v2_20260829/task1"
pids=json.load(open("scripts/t1_full_91_gt_pids.json"))
def gt(c):
    d=json.load(open(f"{GT}/{c}/prostate-biopsy-decision.json"))
    return d.strip().strip('"') if isinstance(d,str) else d.get("biopsy_decision")
def load(d,c):
    p=f"{d}/{c}/prediction.json"
    return json.load(open(p)).get("biopsy_decision") if os.path.exists(p) else "MISSING"
def cm(yt,yp):
    return (sum(1 for t,p in zip(yt,yp) if t=="yes" and p=="yes"),
            sum(1 for t,p in zip(yt,yp) if t=="no" and p=="yes"),
            sum(1 for t,p in zip(yt,yp) if t=="no" and p=="no"),
            sum(1 for t,p in zip(yt,yp) if t=="yes" and p=="no"))
def f1s(tp,fp,tn,fn):
    out={}
    for L,t,f in [("yes",tp,fp),("no",tn,fn)]:
        r = t/(t+f)
        p = t/(t+(fn if L=="yes" else fp))
        out[L]=2*r*p/(r+p) if r+p else 0
    return sum(out.values())/2, out
yt=[gt(c) for c in pids]
yo=[load(OLD,c) for c in pids]; yn=[load(NEW,c) for c in pids]
co,fo=cm(yt,yo); cn,fn=cm(yt,yn)
mo=cm(yt,yo); f1o,_=f1s(*mo); f1n,pn=f1s(*cm(yt,yn))
print(f"0827: TP{co[0]} FP{co[1]} TN{co[2]} FN{co[3]}  F1={f1o:.3f}")
print(f" v2 : TP{cn[0]} FP{cn[1]} TN{cn[2]} FN{cn[3]}  F1={f1n:.3f} (yes={pn['yes']:.3f} no={pn['no']:.3f})")
print(f"ΔF1={f1n-f1o:+.3f}  投影预期 +0.004")
flips=[(c,o,n,gt(c)) for c,o,n in zip(pids,yo,yn) if o!=n]
fx=sum(1 for _,o,n,g in flips if n==g and o!=g)
hx=sum(1 for _,o,n,g in flips if o==g and n!=g)
print(f"翻转 {len(flips)} case: 修对 {fx} / 修错 {hx}")
for c,o,n,g in flips[:20]:
    print(f"  {c}: {o}->{n} GT={g} [{'修对' if n==g else '修错'}]")
PYEOF
log "对比完成"

log "Phase 5 · 停 35B → T1 GEval（gemma）"
pkill -f "llama_cpp[.]server"; sleep 8
sed -e 's|output.t3_full_tau_v2_20260829|output.t1_full_v2_20260829|g' \
    -e 's|overnight_geval_report_step9|night_geval_t1full|g' \
    .geval_tmp/geval_eval_step9.py > .geval_tmp/geval_eval_t1full.py
.venv/bin/python .geval_tmp/geval_eval_t1full.py --full > /tmp/geval_t1full.log 2>&1
log "T1 GEval 完成: scripts/night_geval_t1full.md"
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"gemma4:e4b","keep_alive":0}' > /dev/null 2>&1
log "gemma 已卸载（keep_alive:0）"

log "Phase 6 · 汇总报告"
{
echo "# 夜间全链报告（T1 全量 91 + GEval）"
echo ""
echo "> 生成: $(date '+%Y-%m-%d %H:%M:%S')"
echo "> run: $OUT（91 GT case，双关闭，v2 条款 prompt）"
echo ""
echo "## 1. Run 统计"
echo "- done: $DONE_N / 91；failed: $FAIL_N"
echo "- 反泄漏 'Predictor loaded' 命中: $LEAK（应 0）"
echo "- 末 3 行: "
echo '```'
tail -3 t1_full_run.log
echo '```'
echo ""
echo "## 2. Monitor"
echo '```'
grep -E "HARD|DRIFT|干净|问题" /tmp/trace_monitor_t1full.log | tail -5 || echo "(无告警行)"
echo '```'
echo ""
echo "## 3. 官方 eval（Task1 段）"
echo '```'
sed -n "/Task1/,/Task2/p" scripts/eval_train_gt_report_t1_full_v2_20260829.md | head -18
echo '```'
echo ""
echo "## 4. vs 0827 全量对比"
echo '```'
cat /tmp/t1_full_compare.txt
echo '```'
echo ""
echo "## 5. T1 GEval（gemma4:e4b）"
echo '```'
grep -E "平均|高分|低分|解析失败|Task1" scripts/night_geval_t1full.md 2>/dev/null | head -10 || tail -20 /tmp/geval_t1full.log
echo '```'
} > scripts/night_fullchain_report.md
log "ALL DONE → scripts/night_fullchain_report.md"
