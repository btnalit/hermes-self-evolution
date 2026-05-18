#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTO_YES=false
SKIP_PLUGIN=false
SKIP_CRON=false
SKIP_DEPS=false
SKIP_TEST=false
PYTHON_BIN="${PYTHON_BIN:-python3}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hermes-home)
            HERMES_HOME="$2"
            shift 2
            ;;
        --yes|-y)
            AUTO_YES=true
            shift
            ;;
        --skip-plugin)
            SKIP_PLUGIN=true
            shift
            ;;
        --skip-cron)
            SKIP_CRON=true
            shift
            ;;
        --skip-deps)
            SKIP_DEPS=true
            shift
            ;;
        --skip-test|--skip-tests)
            SKIP_TEST=true
            shift
            ;;
        --help|-h)
            cat <<'EOF'
Usage: bash setup.sh [options]

Options:
  --hermes-home PATH  Install into this Hermes home (default: $HERMES_HOME or ~/.hermes)
  --yes, -y           Accept default yes answers
  --skip-plugin       Do not copy/register the Hermes plugin
  --skip-cron         Do not create a cron entry
  --skip-deps         Do not install Python dependencies
  --skip-test         Do not run dry-run smoke checks
  --help, -h          Show this help
EOF
            exit 0
            ;;
        *)
            echo "Unknown: $1"
            exit 1
            ;;
    esac
done

SKILL_DIR="$HERMES_HOME/skills/dogfood/self-evolution-governor"
STATE_DIR="$HERMES_HOME/state/evolution"
PIPELINE_SCRIPT="$SKILL_DIR/scripts/self_evolution_daily_pipeline.py"
PLUGIN_DST="$HERMES_HOME/plugins/hermes-self-evolution"
export HERMES_HOME

have_cmd() {
    command -v "$1" >/dev/null 2>&1
}

ask_yes_no() {
    local prompt="$1"
    local default="${2:-y}"
    local reply

    if $AUTO_YES; then
        REPLY="$default"
        return 0
    fi

    read -rp "$prompt" reply
    REPLY="${reply:-$default}"
}

run_privileged() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif have_cmd sudo; then
        sudo "$@"
    else
        return 127
    fi
}

detect_package_manager() {
    if have_cmd apt-get; then echo "apt"
    elif have_cmd dnf; then echo "dnf"
    elif have_cmd yum; then echo "yum"
    elif have_cmd apk; then echo "apk"
    elif have_cmd pacman; then echo "pacman"
    elif have_cmd zypper; then echo "zypper"
    elif have_cmd brew; then echo "brew"
    else echo "none"
    fi
}

install_packages() {
    local pm="$1"
    shift
    [ "$#" -gt 0 ] || return 1

    case "$pm" in
        apt)
            run_privileged apt-get update
            run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
            ;;
        dnf) run_privileged dnf install -y "$@" ;;
        yum) run_privileged yum install -y "$@" ;;
        apk) run_privileged apk add --no-cache "$@" ;;
        pacman) run_privileged pacman -Sy --noconfirm "$@" ;;
        zypper) run_privileged zypper --non-interactive install -y "$@" ;;
        brew) brew install "$@" ;;
        *) return 1 ;;
    esac
}

install_package_group() {
    local group="$1"
    local pm
    local pkgs=()

    pm="$(detect_package_manager)"
    if [ "$pm" = "none" ]; then
        echo "      ⚠️  未识别系统包管理器，跳过系统包安装"
        return 1
    fi

    case "$group:$pm" in
        python:apt|python:dnf|python:yum|python:apk|python:zypper) pkgs=(python3) ;;
        python:pacman|python:brew) pkgs=(python) ;;

        pyyaml:apt) pkgs=(python3-yaml) ;;
        pyyaml:dnf) pkgs=(python3-pyyaml) ;;
        pyyaml:yum|pyyaml:zypper) pkgs=(python3-PyYAML) ;;
        pyyaml:apk) pkgs=(py3-yaml) ;;
        pyyaml:pacman) pkgs=(python-yaml) ;;

        pip:apt|pip:dnf|pip:yum|pip:zypper) pkgs=(python3-pip) ;;
        pip:apk) pkgs=(py3-pip) ;;
        pip:pacman) pkgs=(python-pip) ;;
        pip:brew) pkgs=(python) ;;

        cron:apt|cron:zypper) pkgs=(cron) ;;
        cron:dnf|cron:yum|cron:pacman) pkgs=(cronie) ;;
        cron:apk) pkgs=(dcron) ;;
    esac

    if [ "${#pkgs[@]}" -eq 0 ]; then
        echo "      ⚠️  $pm 暂无 $group 的自动安装规则"
        return 1
    fi

    echo "      → 检测到 $pm，安装: ${pkgs[*]}"
    install_packages "$pm" "${pkgs[@]}"
}

print_manual_dependency_hint() {
    local pm
    pm="$(detect_package_manager)"
    echo "   ⚠️  自动安装依赖失败。可手动执行:"
    case "$pm" in
        apt) echo "      sudo apt-get update && sudo apt-get install -y python3 python3-yaml python3-pip cron" ;;
        dnf) echo "      sudo dnf install -y python3 python3-pyyaml python3-pip cronie" ;;
        yum) echo "      sudo yum install -y python3 python3-PyYAML python3-pip cronie" ;;
        apk) echo "      sudo apk add --no-cache python3 py3-yaml py3-pip dcron" ;;
        pacman) echo "      sudo pacman -Sy --noconfirm python python-yaml python-pip cronie" ;;
        zypper) echo "      sudo zypper --non-interactive install -y python3 python3-PyYAML python3-pip cron" ;;
        brew) echo "      brew install python && python3 -m pip install --user pyyaml" ;;
        *) echo "      安装 Python 3 和 PyYAML（Debian/Ubuntu: python3 python3-yaml）" ;;
    esac
}

ensure_python3() {
    if have_cmd "$PYTHON_BIN"; then
        PYTHON_BIN="$(command -v "$PYTHON_BIN")"
        return 0
    fi

    if [ "$PYTHON_BIN" != "python3" ]; then
        echo "      ⚠️  指定的 PYTHON_BIN 不存在: $PYTHON_BIN"
        return 1
    fi

    echo "      → 未找到 python3，尝试自动安装"
    install_package_group python || return 1
    if have_cmd python3; then
        PYTHON_BIN="$(command -v python3)"
        return 0
    fi
    return 1
}

python_has_pyyaml() {
    "$PYTHON_BIN" -c "import yaml" >/dev/null 2>&1
}

ensure_pip() {
    "$PYTHON_BIN" -m pip --version >/dev/null 2>&1 && return 0
    echo "      → 未找到 pip，尝试自动安装"
    install_package_group pip || return 1
    "$PYTHON_BIN" -m pip --version >/dev/null 2>&1
}

install_pyyaml_with_pip() {
    echo -n "      → 尝试 python -m pip --user 安装 pyyaml ... "
    if "$PYTHON_BIN" -m pip install --user pyyaml -q 2>/dev/null; then
        echo "✅"
        return 0
    fi
    echo "失败"

    echo -n "      → 尝试 python -m pip 系统安装 pyyaml ... "
    if "$PYTHON_BIN" -m pip install pyyaml -q 2>/dev/null; then
        echo "✅"
        return 0
    fi
    echo "失败"

    echo -n "      → 尝试 python -m pip --break-system-packages 安装 pyyaml ... "
    if "$PYTHON_BIN" -m pip install --break-system-packages pyyaml -q 2>/dev/null; then
        echo "✅"
        return 0
    fi
    echo "失败"
    return 1
}

ensure_pyyaml() {
    ensure_python3 || return 1

    if python_has_pyyaml; then
        echo "   ✅ pyyaml 已可用 ($PYTHON_BIN)"
        return 0
    fi

    echo "      → 未找到 PyYAML，优先使用系统包安装"
    if install_package_group pyyaml && python_has_pyyaml; then
        echo "   ✅ pyyaml 已通过系统包安装"
        return 0
    fi

    echo "      → 系统包安装不可用，尝试 python -m pip fallback"
    if ensure_pip && install_pyyaml_with_pip && python_has_pyyaml; then
        echo "   ✅ pyyaml 已通过 pip 安装"
        return 0
    fi

    return 1
}

ensure_crontab() {
    have_cmd crontab && return 0
    echo "      → 未找到 crontab，尝试自动安装 cron"
    install_package_group cron || return 1
    have_cmd crontab
}

shell_quote() {
    printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

sed_escape_replacement() {
    printf "%s" "$1" | sed -e 's/[\/&]/\\&/g'
}

replace_in_file() {
    local file="$1"
    local expr="$2"
    local tmp

    [ -f "$file" ] || return 0
    tmp="${file}.tmp.$$"
    if sed "$expr" "$file" > "$tmp" && mv "$tmp" "$file"; then
        return 0
    fi
    rm -f "$tmp"
    echo "   ⚠️  无法更新文件，保留现有版本: $file"
    return 1
}

copy_file_if_needed() {
    local src="$1"
    local dst="$2"
    local err_file
    local err_msg

    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        return 0
    fi

    err_file="${TMPDIR:-/tmp}/self-evolution-copy-error.$$"
    if cp "$src" "$dst" 2>"$err_file"; then
        rm -f "$err_file"
        return 0
    fi

    err_msg="$(cat "$err_file" 2>/dev/null || true)"
    rm -f "$err_file"
    if [ -e "$dst" ]; then
        echo "   ⚠️  无法更新已存在文件，保留现有版本: $dst"
        [ -n "$err_msg" ] && echo "      $err_msg"
        return 0
    fi

    echo "   ❌ 无法安装文件: $dst"
    [ -n "$err_msg" ] && echo "      $err_msg"
    return 1
}

init_state_file() {
    local target="$1"
    local content="$2"

    if [ -e "$target" ]; then
        echo "   ⏭️  保留已有状态文件: $target"
        return 0
    fi

    mkdir -p "$(dirname "$target")"
    printf "%s\n" "$content" > "$target"
    echo "   ✅ 初始化: $target"
}

touch_state_file() {
    local target="$1"

    if [ -e "$target" ]; then
        echo "   ⏭️  保留已有状态文件: $target"
        return 0
    fi

    mkdir -p "$(dirname "$target")"
    : > "$target"
    echo "   ✅ 初始化: $target"
}

rewrite_installed_paths() {
    local escaped_home
    local files=()
    local file

    escaped_home="$(sed_escape_replacement "$HERMES_HOME")"
    files+=("$SKILL_DIR/SKILL.md")
    for file in "$SKILL_DIR/scripts/"*.py; do
        [ -e "$file" ] && files+=("$file")
    done

    for file in "${files[@]}"; do
        if grep -q "/vol1/.hermes" "$file" 2>/dev/null; then
            replace_in_file "$file" "s|/vol1/\\.hermes|$escaped_home|g" || true
        fi
    done
}

register_plugin_in_config() {
    local config_file="$HERMES_HOME/config.yaml"
    local tmp

    if [ ! -f "$config_file" ]; then
        echo "   ⚠️  未找到 $config_file，请在 Hermes 初始化后手动注册插件"
        return 0
    fi

    if grep -q "hermes-self-evolution" "$config_file" 2>/dev/null; then
        echo "   ⏭️  插件已在 config.yaml 中注册"
        return 0
    fi

    tmp="${config_file}.tmp.$$"
    if grep -q "^[[:space:]]*enabled:[[:space:]]*\\[\\]" "$config_file"; then
        sed 's/enabled:[[:space:]]*\[\]/enabled: [hermes-self-evolution]/' "$config_file" > "$tmp"
    elif grep -q "^[[:space:]]*enabled:[[:space:]]*\\[" "$config_file"; then
        sed 's/enabled:[[:space:]]*\[\(.*\)\]/enabled: [\1, hermes-self-evolution]/' "$config_file" > "$tmp"
    elif grep -q "^plugins:" "$config_file"; then
        sed '/^plugins:/a\  enabled: [hermes-self-evolution]' "$config_file" > "$tmp"
    else
        {
            cat "$config_file"
            printf "\nplugins:\n  enabled: [hermes-self-evolution]\n"
        } > "$tmp"
    fi

    if mv "$tmp" "$config_file"; then
        echo "   ✅ 插件已注册到 config.yaml"
    else
        rm -f "$tmp"
        echo "   ⚠️  无法写入 config.yaml，请手动注册 hermes-self-evolution"
    fi
}

install_system_cron() {
    local python_for_cron
    local cron_line
    local existing

    ensure_python3 || {
        echo "   ⚠️  未找到 Python，跳过 cron 创建"
        return 0
    }
    ensure_crontab || {
        echo "   ⚠️  未找到 crontab，且自动安装 cron 失败；请手动创建定时任务"
        return 0
    }

    python_for_cron="$PYTHON_BIN"
    if have_cmd "$PYTHON_BIN"; then
        python_for_cron="$(command -v "$PYTHON_BIN")"
    fi

    cron_line="0 4 * * * cd $(shell_quote "$SKILL_DIR/scripts") && HERMES_HOME=$(shell_quote "$HERMES_HOME") $(shell_quote "$python_for_cron") $(shell_quote "$PIPELINE_SCRIPT")"
    existing="$(crontab -l 2>/dev/null || true)"
    if printf "%s\n" "$existing" | grep -q "self_evolution_daily_pipeline"; then
        echo "   ⏭️  cron 任务已存在"
        return 0
    fi

    {
        printf "%s\n" "$existing"
        printf "%s\n" "# Self-Evolution daily pipeline"
        printf "%s\n" "$cron_line"
    } | crontab -
    echo "   ✅ Cron 任务已添加（系统 crontab，每天 04:00）"
    echo "     查看: crontab -l"
}

echo "=============================="
echo " Self-Evolution Governor 安装向导"
echo "=============================="
echo ""

# ── Step 1: Install skill files ──
echo "📦 [1/5] 安装 skill 文件..."
mkdir -p "$STATE_DIR/score_explanations" "$SKILL_DIR/scripts" "$SKILL_DIR/templates"
copy_file_if_needed "$SOURCE_DIR/skills/self-evolution-governor/SKILL.md" "$SKILL_DIR/SKILL.md"
for src in "$SOURCE_DIR/skills/self-evolution-governor/scripts/"*.py; do
    [ -e "$src" ] || continue
    copy_file_if_needed "$src" "$SKILL_DIR/scripts/$(basename "$src")"
done
if [ -f "$SOURCE_DIR/scripts/self_evolution_daily_pipeline.py" ]; then
    copy_file_if_needed "$SOURCE_DIR/scripts/self_evolution_daily_pipeline.py" "$PIPELINE_SCRIPT"
else
    echo "   ⚠️  未找到 scripts/self_evolution_daily_pipeline.py，cron pipeline 将不可用"
fi
copy_file_if_needed "$SOURCE_DIR/skills/self-evolution-governor/templates/proposal.yaml" "$SKILL_DIR/templates/proposal.yaml"
rewrite_installed_paths
chmod 755 "$SKILL_DIR/scripts/"*.py 2>/dev/null || true
chmod 755 "$STATE_DIR"
echo "   ✅ Skill installed at: $SKILL_DIR"

# ── Step 2: Initialize state files ──
echo -e "\n📁 [2/5] 初始化状态文件..."
touch_state_file "$STATE_DIR/signals.jsonl"
init_state_file "$STATE_DIR/speak_quota.json" '{"date":"","suggestions":0,"strategic":0}'
if [ -f "$STATE_DIR/self_agenda.yaml" ]; then
    echo "   ⏭️  保留已有状态文件: $STATE_DIR/self_agenda.yaml"
else
    cp "$SOURCE_DIR/demo/self_agenda.yaml.example" "$STATE_DIR/self_agenda.yaml"
    echo "   ✅ 初始化: $STATE_DIR/self_agenda.yaml"
fi
init_state_file "$STATE_DIR/proposal_queue.yaml" '{"version":1,"updated_at":"","proposals":[]}'
init_state_file "$STATE_DIR/agenda_candidates.yaml" '{"version":3,"generated_at":"","shadow_mode":true,"candidates":[]}'
init_state_file "$STATE_DIR/evolution_journal.md" "# Self-Evolution Journal"
init_state_file "$STATE_DIR/runtime_digest.md" "# Hermes Runtime Digest
Empty - run pipeline to populate."
init_state_file "$STATE_DIR/HERMES_FOCUS.md" "# HERMES_FOCUS.md
No current focus."
echo "   ✅ State directory ready at: $STATE_DIR"

# ── Step 3: Install plugin ──
echo -e "\n🔌 [3/5] Runtime digest 注入插件"
if $SKIP_PLUGIN; then
    echo "   ⏭️  --skip-plugin，跳过插件安装"
elif have_cmd hermes; then
    ask_yes_no "   是否安装 Hermes 插件? [Y/n] " "y"
    if [[ "$REPLY" =~ ^[Yy] ]]; then
        mkdir -p "$HERMES_HOME/plugins"
        PLUGIN_SRC="$SOURCE_DIR/plugin/hermes-self-evolution"
        if [ -d "$PLUGIN_DST" ]; then
            echo "   ⏭️  插件目录已存在，跳过复制"
        else
            cp -r "$PLUGIN_SRC" "$PLUGIN_DST"
            echo "   ✅ 插件已复制到 $PLUGIN_DST"
        fi
        register_plugin_in_config
    else
        echo "   ⏭️  跳过插件安装"
    fi
else
    echo "   ⏭️  hermes 未安装，跳过插件"
fi

# ── Step 4: Cron setup ──
echo -e "\n⏰ [4/5] 定时任务设置"
echo "   Pipeline 每天 04:00 自动运行"
if $SKIP_CRON; then
    echo "   ⏭️  --skip-cron，跳过 cron 设置"
else
    ask_yes_no "   是否创建 cron 任务? [Y/n] " "y"
    if [[ "$REPLY" =~ ^[Yy] ]]; then
        INSTALLED=false
        if have_cmd hermes; then
            hermes cron create --name "self-evolution-daily" \
                --schedule "0 4 * * *" \
                --script "$PIPELINE_SCRIPT" \
                --no-agent 2>/dev/null && INSTALLED=true || true
        fi
        if ! $INSTALLED; then
            install_system_cron
        fi
    else
        echo "   ⏭️  跳过 cron 设置"
    fi
fi

# ── Step 5: Install deps + dry-run smoke test ──
echo -e "\n🧪 [5/5] 安装依赖 + dry-run 测试"
DEPS_OK=true
if $SKIP_DEPS; then
    echo "   ⏭️  --skip-deps，跳过依赖安装"
    ensure_python3 >/dev/null 2>&1 || DEPS_OK=false
    python_has_pyyaml || DEPS_OK=false
else
    ask_yes_no "   是否安装依赖? [Y/n] " "y"
    if [[ "$REPLY" =~ ^[Yy] ]]; then
        echo "   ▶ 安装 Python 依赖..."
        if ! ensure_pyyaml; then
            DEPS_OK=false
            print_manual_dependency_hint
        fi
    else
        echo "   ⏭️  跳过依赖安装"
        ensure_python3 >/dev/null 2>&1 || DEPS_OK=false
        python_has_pyyaml || DEPS_OK=false
    fi
fi

if $SKIP_TEST; then
    echo "   ⏭️  --skip-test，跳过测试"
elif ! $DEPS_OK; then
    echo "   ⚠️  依赖未就绪，跳过 dry-run 测试"
else
    ask_yes_no "   是否运行 dry-run 测试? [Y/n] " "y"
    if [[ "$REPLY" =~ ^[Yy] ]]; then
        echo "   ▶ 编译脚本..."
        PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m py_compile "$SKILL_DIR/scripts/"*.py
        echo "   ▶ 收集信号 dry-run（不写 signals.jsonl）..."
        (
            cd "$SKILL_DIR/scripts"
            PYTHONDONTWRITEBYTECODE=1 COLLECT_DRY_RUN=1 "$PYTHON_BIN" collect_signals.py --dry-run
        ) | tail -20
        echo "   ✅ dry-run 测试完成"
    else
        echo "   ⏭️  跳过测试"
    fi
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
echo "   • 跑 pipeline:  $PYTHON_BIN $PIPELINE_SCRIPT"
echo "   • 查看议程:     cat $STATE_DIR/self_agenda.yaml"
echo "=============================="
