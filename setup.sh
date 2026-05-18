#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTO_YES=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hermes-home) HERMES_HOME="$2"; shift 2 ;;
        --yes|-y) AUTO_YES=true; shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

SKILL_DIR="$HERMES_HOME/skills/dogfood/self-evolution-governor"
STATE_DIR="$HERMES_HOME/state/evolution"
PIPELINE_SCRIPT="$SKILL_DIR/scripts/self_evolution_daily_pipeline.py"

echo "=============================="
echo " Self-Evolution Governor 安装向导"
echo "=============================="
echo ""

# ── Step 1: Install skill files ──
echo "📦 [1/5] 安装 skill 文件..."
mkdir -p "$STATE_DIR/score_explanations" "$SKILL_DIR/scripts" "$SKILL_DIR/templates"
cp "$SOURCE_DIR/skills/self-evolution-governor/SKILL.md" "$SKILL_DIR/"
cp "$SOURCE_DIR/skills/self-evolution-governor/scripts/"*.py "$SKILL_DIR/scripts/"
cp "$SOURCE_DIR/skills/self-evolution-governor/templates/proposal.yaml" "$SKILL_DIR/templates/"
chmod 755 "$STATE_DIR"
echo "   ✅ Skill installed at: $SKILL_DIR"

# ── Step 2: Initialize state files ──
echo -e "\n📁 [2/5] 初始化状态文件..."
[ -f "$STATE_DIR/signals.jsonl" ]      || touch "$STATE_DIR/signals.jsonl"
[ -f "$STATE_DIR/speak_quota.json" ]   || echo '{"date":"","suggestions":0,"strategic":0}' > "$STATE_DIR/speak_quota.json"
[ -f "$STATE_DIR/self_agenda.yaml" ]   || cp "$SOURCE_DIR/demo/self_agenda.yaml.example" "$STATE_DIR/self_agenda.yaml"
[ -f "$STATE_DIR/proposal_queue.yaml" ] || echo '{"version":1,"updated_at":"","proposals":[]}' > "$STATE_DIR/proposal_queue.yaml"
echo '{"version":3,"generated_at":"","shadow_mode":true,"candidates":[]}' > "$STATE_DIR/agenda_candidates.yaml" 2>/dev/null || true
[ -f "$STATE_DIR/evolution_journal.md" ] || echo "# Self-Evolution Journal\n" > "$STATE_DIR/evolution_journal.md"
[ -f "$STATE_DIR/runtime_digest.md" ]   || echo "# Hermes Runtime Digest\nEmpty — run pipeline to populate.\n" > "$STATE_DIR/runtime_digest.md"
[ -f "$STATE_DIR/HERMES_FOCUS.md" ]    || echo "# HERMES_FOCUS.md\nNo current focus.\n" > "$STATE_DIR/HERMES_FOCUS.md"
echo "   ✅ State directory ready at: $STATE_DIR"

# ── Step 3: Install plugin ──
echo -e "\n🔌 [3/5] Runtime digest 注入插件"
if [ -x "$(command -v hermes)" ]; then
    PLUGIN_DST="$HERMES_HOME/plugins/hermes-self-evolution"
    if $AUTO_YES; then REPLY="y"; else read -rp "   是否安装 Hermes 插件? [Y/n] " REPLY; fi
    REPLY="${REPLY:-y}"
    if [[ "$REPLY" =~ ^[Yy] ]]; then
        # 直接用复制安装，避免 hermes plugins install 的 Git URL 限制
        mkdir -p "$HERMES_HOME/plugins"
        rm -rf "$PLUGIN_DST"
        cp -r "$SOURCE_DIR/plugin/hermes-self-evolution" "$PLUGIN_DST"
        echo "   ✅ 插件已复制到 $PLUGIN_DST"
        echo "   ▶ 用以下命令激活:"
        echo "     hermes plugins install $PLUGIN_DST --enable"
        # 尝试激活
        hermes plugins install "$PLUGIN_DST" --enable 2>/dev/null && \
            echo "   ✅ 插件激活成功" || \
            echo "   ⚠️  自动激活失败，请手动运行上面命令"
    else
        echo "   ⏭️  跳过插件安装"
    fi
else
    echo "   ⏭️  hermes 未安装，跳过插件"
fi

# ── Step 4: Cron setup ──
echo -e "\n⏰ [4/5] 定时任务设置"
echo "   Pipeline 每天 04:00 自动运行"
if $AUTO_YES; then REPLY="y"; else read -rp "   是否创建 cron 任务? [Y/n] " REPLY; fi
REPLY="${REPLY:-y}"
if [[ "$REPLY" =~ ^[Yy] ]]; then
    # 检查是否已有
    if crontab -l 2>/dev/null | grep -q "self_evolution_daily_pipeline"; then
        echo "   ⏭️  cron 任务已存在"
    else
        # 尝试 hermes cron create，不成功就 fallback 到 crontab
        INSTALLED=false
        if command -v hermes &>/dev/null; then
            # hermes cron 语法因版本而异，尝试几种模式
            hermes cron create --name "self-evolution-daily" \
                --schedule "0 4 * * *" \
                --script "$PIPELINE_SCRIPT" \
                --no-agent 2>/dev/null && INSTALLED=true || true
        fi
        if ! $INSTALLED; then
            # fallback: 系统 crontab
            (crontab -l 2>/dev/null; echo "# Self-Evolution daily pipeline"; \
             echo "0 4 * * * cd $SKILL_DIR/scripts && HERMES_HOME=$HERMES_HOME python3 $PIPELINE_SCRIPT") | crontab -
            echo "   ✅ Cron 任务已添加（系统 crontab，每天 04:00）"
            echo "     查看: crontab -l"
        fi
    fi
else
    echo "   ⏭️  跳过 cron 设置"
fi

# ── Step 5: Install deps + Smoke test ──
echo -e "\n🧪 [5/5] 安装依赖 + 运行测试"
if $AUTO_YES; then REPLY="y"; else read -rp "   是否安装依赖并运行测试? [Y/n] " REPLY; fi
REPLY="${REPLY:-y}"
if [[ "$REPLY" =~ ^[Yy] ]]; then
    # 安装 pyyaml
    echo "   ▶ 安装 Python 依赖..."
    pip3 install pyyaml -q 2>&1 | tail -1 || echo "   ⚠️  pyyaml 安装失败，部分功能可能受限"

    # 测试采集
    echo "   ▶ 收集信号..."
    cd "$SKILL_DIR/scripts"
    python3 collect_signals.py 2>&1 | tail -3

    # 验证
    SIG_COUNT=$(wc -l < "$STATE_DIR/signals.jsonl" 2>/dev/null || echo 0)
    if [ "$SIG_COUNT" -gt 0 ]; then
        echo "   ✅ 测试通过 — signals.jsonl: $SIG_COUNT 行"
        echo "   ▶ 查看 runtime digest: cat $STATE_DIR/runtime_digest.md"
    else
        echo "   ⚠️  signals.jsonl 为空。可能原因：首次运行信号较少，属于正常"
        echo "      cron 每天 04:00 跑一次后会积累数据"
    fi
else
    echo "   ⏭️  跳过测试"
fi

# ── Summary ──
echo ""
echo "=============================="
echo " ✅ 安装完成"
echo "=============================="
echo "   Skill:        $SKILL_DIR"
echo "   状态文件:      $STATE_DIR"
echo "   Pipeline脚本: $PIPELINE_SCRIPT"
[ -d "$PLUGIN_DST" ] && echo "   插件:          $PLUGIN_DST"
echo ""
echo "   手动命令:"
echo "   • 加载 skill:   hermes -s self-evolution-governor"
echo "   • 跑 pipeline:  python3 $PIPELINE_SCRIPT"
echo "   • 查看议程:     cat $STATE_DIR/self_agenda.yaml"
echo "=============================="
