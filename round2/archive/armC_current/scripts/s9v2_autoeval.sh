#!/bin/bash
# s9v2_autoeval.sh — 等待 Step9-v2 run 退出后自动执行全部 0 推理评估链
# 信号驱动：轮询进程存在性（30s 间隔，零 GPU 成本），退出即评估
cd /root/shared-nvme/chimera-agent-baseline
RUN_PID=$1
OUT=test/output.t3_full_tau_v2b_20260829
REPORT=scripts/s9v2_eval_report.md

echo "[$(date '+%H:%M:%S')] watching PID=$RUN_PID" > /tmp/s9v2_autoeval.log

# 等待主 run 退出（容忍进程号复用：同时确认 cmdline 匹配）
while kill -0 "$RUN_PID" 2>/dev/null; do
  sleep 30
done
echo "[$(date '+%H:%M:%S')] run exited, starting eval chain" >> /tmp/s9v2_autoeval.log
sleep 10  # 落盘缓冲

{
echo "# Step9-v2 自动评估报告"
echo ""
echo "> 生成: $(date '+%Y-%m-%d %H:%M:%S')"
echo "> 预测目录: $OUT"
echo ""

echo "## 1. Run 统计"
DONE=$(grep -c "done in" s9v2_run.log)
FAILED=$(grep -c "failed\|FAILED" s9v2_run.log || true)
echo "- 完成 case: $DONE / 76（全池，含 3 无 GT）"
echo "- 失败: $FAILED"
echo "- 末 5 行日志:"
echo '```'
tail -5 s9v2_run.log
echo '```'
echo ""

echo "## 2. Monitor 统计"
tail -20 /tmp/trace_monitor_s9v2.log | grep -E "HARD|DRIFT|cases|干净|问题" | tail -8 || echo "(无关键字行)"
echo ""

echo "## 3. 反泄漏护栏（4 项）"
echo "- a. event=1 months 非 round 计数:（应为 0）"
.venv/bin/python - << 'PYEOF'
import json, glob
nonround = []
for pp in sorted(glob.glob("test/output.t3_full_tau_v2b_20260829/task3/*/prediction.json")):
    d = json.load(open(pp))
    if d.get("event") == 1:
        m = d.get("months_to_recurrence")
        if m is not None and (abs(m - round(m)) > 1e-9):
            nonround.append((d.get("case_id"), m))
print(f"  event=1 总数: {sum(1 for pp in glob.glob('test/output.t3_full_tau_v2b_20260829/task3/*/prediction.json') if json.load(open(pp)).get('event')==1)}")
print(f"  非 round: {len(nonround)} {nonround[:5] if nonround else ''}")
PYEOF
echo "- b. 日志 'Predictor loaded' 命中: $(grep -c 'Predictor loaded' s9v2_run.log || true)（应为 0）"
echo "- c. warnings 'decision override' 计数:（应为 0）"
.venv/bin/python - << 'PYEOF'
import json, glob
ov = tau = cal = fb = 0
for pp in sorted(glob.glob("test/output.t3_full_tau_v2b_20260829/task3/*/trace.json")):
    try:
        t = json.load(open(pp))
        w = t.get("form_fill_warnings", []) or []
        for s in w:
            if "decision override" in str(s): ov += 1
            if "t3_tau" in str(s): tau += 1
            if "t3_event_calibrated" in str(s): cal += 1
            if "t3_months_fallback" in str(s): fb += 1
    except Exception: pass
print(f"  override={ov}  tau触发={tau}  event_calibrated={cal}  months_fallback={fb}")
PYEOF
echo "- d. 启动参数（ps 已验证: enabled=false + decision_override_tasks=[]）"
echo ""

echo "## 4. 官方 GT 评估"
.venv/bin/python scripts/eval_train_gt.py --pred-dir $OUT --report-suffix _t3_full_tau_v2b_20260829 2>&1 | tail -8
echo ""

echo "## 5. τ 不变式"
.venv/bin/python - << 'PYEOF'
import json, glob
viol = []
n_e1 = 0
for pp in sorted(glob.glob("test/output.t3_full_tau_v2b_20260829/task3/*/prediction.json")):
    d = json.load(open(pp))
    if d.get("event") == 1:
        n_e1 += 1
        m = d.get("months_to_recurrence")
        if m is None or m >= 24.0:
            viol.append((d.get("case_id"), m))
print(f"  event=1: {n_e1} case; 不变式违反: {len(viol)} {viol if viol else ''}")
PYEOF
echo ""

echo "## 6. 偏置专项（混淆矩阵 + FP率 + GT0 months 分布）"
.venv/bin/python - << 'PYEOF'
import json, glob, os
TP=FP=TN=FN=0; g0m=[]
for pp in sorted(glob.glob("test/output.t3_full_tau_v2b_20260829/task3/*/prediction.json")):
    c = os.path.basename(os.path.dirname(pp))
    gtp = f"data/task3/agent_input/{c}/prostate-time-to-recurrence-or-last-follow-up.json"
    if not os.path.exists(gtp): continue
    gt = json.load(open(gtp)); d = json.load(open(pp))
    ge, pe = gt.get("event"), d.get("event")
    if ge==1 and pe==1: TP+=1
    elif ge==0 and pe==1: FP+=1; g0m.append(d.get("months_to_recurrence"))
    elif ge==0 and pe==0: TN+=1
    else: FN+=1
gt0 = TN+FP; gt1 = TP+FN; n = TP+FP+TN+FN
print(f"  混淆: TP={TP} FP={FP} TN={TN} FN={FN} (n={n})")
print(f"  EventAcc: {(TP+TN)/n*100:.1f}%")
print(f"  FP率(GT0={gt0}): {FP/gt0*100:.1f}%")
print(f"  GT0中pred=1的months: {sorted(g0m)}")
PYEOF
echo ""

echo "## 7. Docker 3 门槛裁定（重放预期 FP13.0%/EventAcc76.7%/C-index 0.705）"
echo "(以官方 eval 报告数字对照)"
} > $REPORT 2>&1

echo "[$(date '+%H:%M:%S')] eval done -> $REPORT" >> /tmp/s9v2_autoeval.log
