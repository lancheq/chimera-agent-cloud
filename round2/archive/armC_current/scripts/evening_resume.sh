log "(resume) === Phase 2: 重启 35B ==="
nohup .venv/bin/python -m llama_cpp.server \
  --model model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8765 --n_gpu_layers -1 --n_ctx 16384 \
  > qwen_restart_evening.log 2>&1 < /dev/null &
for i in $(seq 1 60); do
  sleep 10
  curl -s --max-time 5 http://127.0.0.1:8765/v1/models | grep -q "Qwen" && break
done
curl -s --max-time 5 http://127.0.0.1:8765/v1/models | grep -q "Qwen" && log "35B ready" || { log "35B FAILED to start"; exit 1; }

log "=== Phase 3: T1-L3 轻量 30 case（v2 prompt，双关闭）==="
# 构建 30-case 分层样本（从 0827 final 分类推导，0 推理）
.venv/bin/python << 'PYEOF'
import json, glob, os, random
GT="data/task1/agent_input"; PRED="test/output.final_20260826/task1"
A1={"0615b2bd8504","11345b8a7ab3","11fe9c6ca833","01b643727538","4b950525e6a8","c9d31197c533","cce5d47c05dd","dcd61cf9b443"}
fn=[];fp_a1=[];fp_a2=[];tp=[];tn=[]
for pp in sorted(glob.glob(f"{PRED}/*/prediction.json")):
    c=os.path.basename(os.path.dirname(pp))
    gp=f"{GT}/{c}/prostate-biopsy-decision.json"
    if not os.path.exists(gp): continue
    d=json.load(open(gp)); g=d.strip().strip('"') if isinstance(d,str) else d.get("biopsy_decision")
    p=json.load(open(pp)).get("biopsy_decision")
    cid=c.replace("PT-pseudo_","")
    if g=="yes" and p=="no": fn.append(c)
    elif g=="no" and p=="yes": (fp_a1 if cid in A1 else fp_a2).append(c)
    elif g=="yes": tp.append(c)
    else: tn.append(c)
random.seed(42)
tp_s=[c for c in tp if "b9f0b7018502" in c or "0cdfb9410718" in c]+random.sample([c for c in tp if "b9f0b7018502" not in c and "0cdfb9410718" not in c],4)
sample=fn+random.sample(fp_a1,4)+random.sample(fp_a2,4)+tp_s+random.sample(tn,6)
json.dump(sample, open("scripts/t1_l3light_sample_20260829.json","w"))
print(f"sample={len(sample)} FN{len(fn)} A1x4 A2x4 TP{len(tp_s)} TNx6")
PYEOF
PIDS=$(.venv/bin/python -c "import json;print(','.join(json.load(open('scripts/t1_l3light_sample_20260829.json'))))")
nohup .venv/bin/python -m chimera_agent_baseline.run +experiment=qwen_local \
  agent.tasks=[1] "agent.pids=[$PIDS]" \
  agent.predictor.enabled=false agent.predictor.decision_override_tasks=[] \
  agent.form_fill.self_refine=false agent.form_fill.atomic_check=false \
  paths.output_dir=test/output.t1_l3light_20260829 > t1_l3light_run.log 2>&1 < /dev/null &
log "T1-L3light launched, waiting..."
while pgrep -f "output.t1_l3light_20260829" | grep -qv $$; do sleep 30; done 2>/dev/null
# 等待 run 完成（轮询 cmdline 精确匹配）
while ps aux | grep "paths.output_dir=test/output.t1_l3light_20260829" | grep -v grep > /dev/null; do sleep 30; done
log "T1-L3light done"

log "=== Phase 3b: 轻量评估 + gate ==="
GATE=$(.venv/bin/python << 'PYEOF'
import json, glob, os
GT="data/task1/agent_input"; PRED="test/output.t1_l3light_20260829/task1"
sample=json.load(open("scripts/t1_l3light_sample_20260829.json"))
cls={}
for c in sample:
    d=json.load(open(f"{GT}/{c}/prostate-biopsy-decision.json"))
    g=d.strip().strip('"') if isinstance(d,str) else d.get("biopsy_decision")
    pp=f"{PRED}/{c}/prediction.json"
    p=json.load(open(pp)).get("biopsy_decision") if os.path.exists(pp) else "MISSING"
    cls[c]=(g,p)
fn_fix=sum(1 for c,(g,p) in cls.items() if g=="yes" and p=="yes" and c in json.load(open("scripts/t1_l3light_sample_20260829.json")))
# 分类统计
fn_tot=tp_h=tn_h=fp_fix=tp_tot=tn_tot=fn_tot_g=fp_tot=0
old=json.load(open("scripts/t1_l3light_sample_20260829.json"))
# 0827 分类
PRED27="test/output.final_20260826/task1"
oldcls={}
for c in old:
    d=json.load(open(f"{GT}/{c}/prostate-biopsy-decision.json"))
    g=d.strip().strip('"') if isinstance(d,str) else d.get("biopsy_decision")
    p=json.load(open(f"{PRED27}/{c}/prediction.json")).get("biopsy_decision")
    oldcls[c]=(g,p)
fn=[c for c in old if oldcls[c]==("yes","no")]
fp=[c for c in old if oldcls[c]==("no","yes")]
tp=[c for c in old if oldcls[c]==("yes","yes")]
tn=[c for c in old if oldcls[c]==("no","no")]
fn_fix=sum(1 for c in fn if cls[c][1]=="yes")
fp_fix=sum(1 for c in fp if cls[c][1]=="no")
tp_h=sum(1 for c in tp if cls[c][1]=="yes")
tn_h=sum(1 for c in tn if cls[c][1]=="no")
print(f"STATS FN翻正={fn_fix}/{len(fn)} FP翻正={fp_fix}/{len(fp)} TP保持={tp_h}/{len(tp)} TN保持={tn_h}/{len(tn)}")
g1=fn_fix>=4; g2=fp_fix>=3; g3=tp_h>=len(tp)-1; g4=tn_h>=len(tn)-1
print(f"GATE fn>=4:{g1} fp>=3:{g2} tp保持:{g3} tn保持:{g4}")
print("PASS" if all([g1,g2,g3,g4]) else "FAIL")
PYEOF
)
echo "$GATE"
if echo "$GATE" | grep -q PASS; then
  log "GATE PASS → Phase 4: T1 全量 91"
  nohup .venv/bin/python -m chimera_agent_baseline.run +experiment=qwen_local \
    agent.tasks=[1] \
    agent.predictor.enabled=false agent.predictor.decision_override_tasks=[] \
    agent.form_fill.self_refine=false agent.form_fill.atomic_check=false \
    paths.output_dir=test/output.t1_full_v2_20260829 > t1_full_run.log 2>&1 < /dev/null &
  while ps aux | grep "paths.output_dir=test/output.t1_full_v2_20260829" | grep -v grep > /dev/null; do sleep 60; done
  log "T1 full done"
  .venv/bin/python scripts/eval_train_gt.py --pred-dir test/output.t1_full_v2_20260829 --report-suffix _t1_full_v2_20260829 > /dev/null 2>&1
  log "T1 full eval done"
else
  log "GATE FAIL → 停住（轻量结果保留，不烧全量）"
fi

log "=== Phase 5: 汇总 ==="
{
echo "# 晚间管线报告"
echo ""
echo "## GEval (Step9-v2 v2b, gemma4:e4b)"
tail -30 scripts/evening_geval_v2b.md 2>/dev/null
echo ""
echo "## T1-L3 轻量 (30 case, v2 prompt)"
echo '```'
echo "$GATE"
echo '```'
echo ""
echo "## T1 全量 (91 case, 若 gate PASS)"
sed -n "/Task1/,/Task2/p" scripts/eval_train_gt_report_t1_full_v2_20260829.md 2>/dev/null | head -15
} > $R
log "ALL DONE → $R"
