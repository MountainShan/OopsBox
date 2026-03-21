#!/bin/bash

# 讀取輸入
input=$(cat)

# 快取檔案路徑
CACHE_DIR="$HOME/.claude/cache"
GIT_CACHE="$CACHE_DIR/git_branch"
mkdir -p "$CACHE_DIR"

# 基本資訊提取 - 使用單一 jq 調用
read -r MODEL SESSION_ID CURRENT_DIR TRANSCRIPT_PATH <<< $(echo "$input" | jq -r '
    .model.display_name,
    .session_id,
    .workspace.current_dir,
    (.transcript_path // "")
' | tr '\n' ' ')

PROJECT_NAME=$(basename "$CURRENT_DIR")

# 根據模型設定顏色和圖標
case "$MODEL" in
    *"Opus"*)
        MODEL_COLOR="\\033[38;2;195;158;83m"
        MODEL_ICON="💛"
        ;;
    *"Sonnet"*)
        MODEL_COLOR="\\033[38;2;118;170;185m"
        MODEL_ICON="💠"
        ;;
    *"Haiku"*)
        MODEL_COLOR="\\033[38;2;255;182;193m"
        MODEL_ICON="🌸"
        ;;
esac

COLOR_RESET="\\033[0m"
MESSAGE_COLOR="\\033[38;2;152;195;121m"

# Git 分支快取機制（5秒有效期）
BRANCH=""
if git rev-parse --git-dir > /dev/null 2>&1; then
    current_time=$(date +%s)

    # 檢查快取是否有效
    if [ -f "$GIT_CACHE" ]; then
        cache_time=$(stat -f %m "$GIT_CACHE" 2>/dev/null || stat -c %Y "$GIT_CACHE" 2>/dev/null)
        if [ $((current_time - cache_time)) -lt 5 ]; then
            BRANCH=$(cat "$GIT_CACHE")
        fi
    fi

    # 快取過期或不存在，重新獲取
    if [ -z "$BRANCH" ]; then
        BRANCH_NAME=$(git branch --show-current 2>/dev/null)
        if [ -n "$BRANCH_NAME" ]; then
            BRANCH=" ⚡ $BRANCH_NAME"
        fi
        echo "$BRANCH" > "$GIT_CACHE"
    fi
fi

# Session 追蹤目錄
TRACKER_DIR="$HOME/.claude/session-tracker"
SESSIONS_DIR="$TRACKER_DIR/sessions"
mkdir -p "$SESSIONS_DIR"

# 當前時間
CURRENT_TIME=$(date +%s)
TODAY=$(date +%Y-%m-%d)

# 優化的 session 更新函數
update_session() {
    local session_file="$SESSIONS_DIR/$SESSION_ID.json"

    if [ ! -f "$session_file" ]; then
        cat > "$session_file" <<EOF
{
    "id": "$SESSION_ID",
    "date": "$TODAY",
    "start": $CURRENT_TIME,
    "last_heartbeat": $CURRENT_TIME,
    "total_seconds": 0,
    "intervals": [{"start": $CURRENT_TIME, "end": null}]
}
EOF
    else
        jq --argjson now "$CURRENT_TIME" '
            . as $orig |
            ($now - .last_heartbeat) as $gap |
            .last_heartbeat = $now |
            if $gap < 600 then
                .intervals[-1].end = $now
            else
                .intervals += [{"start": $now, "end": $now}]
            end |
            .total_seconds = ([.intervals[] | if .end != null then (.end - .start) else 0 end] | add // 0)
        ' "$session_file" > "$session_file.tmp" && mv "$session_file.tmp" "$session_file"
    fi
}

# 計算所有 session 總時數
calculate_total_hours() {
    local total_seconds=0
    local active_sessions=0

    while IFS= read -r -d '' session_file; do
        read -r session_date session_seconds last_heartbeat <<< $(jq -r '
            .date // "",
            (.total_seconds // 0),
            (.last_heartbeat // 0)
        ' "$session_file" 2>/dev/null | tr '\n' ' ')

        if [ "$session_date" = "$TODAY" ] && [ -n "$session_seconds" ]; then
            total_seconds=$((total_seconds + session_seconds))
            if [ $((CURRENT_TIME - last_heartbeat)) -lt 600 ]; then
                active_sessions=$((active_sessions + 1))
            fi
        fi
    done < <(find "$SESSIONS_DIR" -name "*.json" -print0 2>/dev/null)

    local hours=$((total_seconds / 3600))
    local minutes=$(((total_seconds % 3600) / 60))

    local time_str=""
    if [ $hours -gt 0 ]; then
        time_str="${hours}h"
        [ $minutes -gt 0 ] && time_str="${time_str}${minutes}m"
    else
        time_str="${minutes}m"
    fi

    [ $active_sessions -gt 1 ] && echo "$time_str [$active_sessions sessions]" || echo "$time_str"
}

# 歸檔舊 session
archive_old_sessions() {
    find "$SESSIONS_DIR" -name "*.json" -exec sh -c '
        for file; do
            session_date=$(jq -r ".date // \"\"" "$file" 2>/dev/null)
            if [ "$session_date" != "'"$TODAY"'" ] && [ -n "$session_date" ]; then
                archive_dir="'"$TRACKER_DIR"'/archive/$session_date"
                mkdir -p "$archive_dir"
                mv "$file" "$archive_dir/"
            fi
        done
    ' sh {} +
}

# Context 使用量計算
calculate_context_usage() {
    local transcript_path="$1"
    [ ! -f "$transcript_path" ] && { echo "0"; return; }

    tail -100 "$transcript_path" 2>/dev/null | awk '
        {
            if (match($0, /"isSidechain":[[:space:]]*false/) &&
                match($0, /"usage":[[:space:]]*\{/)) {
                input_tokens = 0
                cache_read = 0
                cache_creation = 0
                if (match($0, /"input_tokens":[[:space:]]*([0-9]+)/, arr))
                    input_tokens = arr[1]
                if (match($0, /"cache_read_input_tokens":[[:space:]]*([0-9]+)/, arr))
                    cache_read = arr[1]
                if (match($0, /"cache_creation_input_tokens":[[:space:]]*([0-9]+)/, arr))
                    cache_creation = arr[1]
                context_length = input_tokens + cache_read + cache_creation
                if (context_length > 0) {
                    print context_length
                    exit
                }
            }
        }
        END { if (NR == 0 || context_length == 0) print "0" }
    '
}

# 使用者訊息提取
extract_last_user_message() {
    local transcript_path="$1"
    local current_session_id="$2"
    [ ! -f "$transcript_path" ] && return

    tail -200 "$transcript_path" 2>/dev/null | tac | awk -v session_id="$current_session_id" '
        /^$/ { next }
        {
            if (!match($0, /^\{.*\}$/)) next
            is_sidechain = match($0, /"isSidechain":[[:space:]]*true/)
            session_match = match($0, /"sessionId":[[:space:]]*"'"'"'"$current_session_id"'"'"'"/)
            is_user = match($0, /"role":[[:space:]]*"user"/) && match($0, /"type":[[:space:]]*"user"/)
            if (!is_sidechain && session_match && is_user) {
                if (match($0, /"content":[[:space:]]*"([^"]*)"/, arr)) {
                    content = arr[1]
                    if (match(content, /^[\[\{].*[\]\}]$/) ||
                        match(content, /<(local-command-stdout|command-name|command-message|command-args)>/) ||
                        match(content, /^Caveat:/) ||
                        content == "" || content == "null") {
                        next
                    }
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", content)
                    if (length(content) > 0) {
                        print content
                        exit
                    }
                }
            }
        }
    '
}

# 格式化使用者訊息
format_user_message() {
    local message="$1"
    [ -z "$message" ] && return
    echo "$message" | awk '
        BEGIN { max_lines = 3; line_width = 80; line_count = 0 }
        line_count < max_lines {
            line_count++
            if (length($0) > line_width) {
                $0 = substr($0, 1, 77) "..."
            }
            print $0
        }
        END {
            if (NR > max_lines) {
                print "... (還有 " (NR - max_lines) " 行)"
            }
        }
    '
}

# 數字格式化
format_number() {
    local num="$1"
    [ -z "$num" ] || [ "$num" = "0" ] && { echo "--"; return; }
    if [ "$num" -ge 1000000 ]; then
        echo "$((num / 1000000))M"
    elif [ "$num" -ge 1000 ]; then
        echo "$((num / 1000))k"
    else
        echo "$num"
    fi
}

# 進度條生成
generate_progress_bar() {
    local percentage="$1"
    local width=10
    local filled=$(( percentage * width / 100 ))
    [ "$filled" -lt 0 ] && filled=0
    [ "$filled" -gt "$width" ] && filled=$width
    local empty=$((width - filled))
    local bar_color=$(get_context_color "$percentage")
    local gray_color="\\033[38;2;64;64;64m"
    local bar=""
    if [ $filled -gt 0 ]; then
        bar="${bar}${bar_color}"
        for ((i=0; i<filled; i++)); do bar="${bar}█"; done
        bar="${bar}${COLOR_RESET}"
    fi
    if [ $empty -gt 0 ]; then
        bar="${bar}${gray_color}"
        for ((i=0; i<empty; i++)); do bar="${bar}░"; done
        bar="${bar}${COLOR_RESET}"
    fi
    echo "$bar"
}

# Context 顏色設定
get_context_color() {
    local percentage="$1"
    [ -z "$percentage" ] && { echo "\\033[38;2;192;192;192m"; return; }
    if [ "$percentage" -lt 60 ]; then
        echo "\\033[38;2;108;167;108m"
    elif [ "$percentage" -lt 80 ]; then
        echo "\\033[38;2;188;155;83m"
    else
        echo "\\033[38;2;185;102;82m"
    fi
}

# 執行主要邏輯
update_session
archive_old_sessions
TOTAL_HOURS=$(calculate_total_hours)

# Context 使用量計算
CONTEXT_USAGE=""
USER_MESSAGE_DISPLAY=""
if [ -n "$TRANSCRIPT_PATH" ] && [ "$TRANSCRIPT_PATH" != "null" ] && [ "$TRANSCRIPT_PATH" != "" ]; then
    CONTEXT_LENGTH=$(calculate_context_usage "$TRANSCRIPT_PATH")

    if [ -n "$CONTEXT_LENGTH" ] && [ "$CONTEXT_LENGTH" != "0" ]; then
        CONTEXT_PERCENTAGE=$((CONTEXT_LENGTH * 100 / 200000))
        [ "$CONTEXT_PERCENTAGE" -gt 100 ] && CONTEXT_PERCENTAGE=100
        PROGRESS_BAR=$(generate_progress_bar "$CONTEXT_PERCENTAGE")
        FORMATTED_NUM=$(format_number "$CONTEXT_LENGTH")
        CONTEXT_COLOR=$(get_context_color "$CONTEXT_PERCENTAGE")
        CONTEXT_USAGE=" | ${PROGRESS_BAR} ${CONTEXT_COLOR}${CONTEXT_PERCENTAGE}% ${FORMATTED_NUM}${COLOR_RESET}"
    fi

    LAST_USER_MESSAGE=$(extract_last_user_message "$TRANSCRIPT_PATH" "$SESSION_ID")
    if [ -n "$LAST_USER_MESSAGE" ]; then
        FORMATTED_USER_MESSAGE=$(format_user_message "$LAST_USER_MESSAGE")
        if [ -n "$FORMATTED_USER_MESSAGE" ]; then
            USER_MESSAGE_DISPLAY=$(echo "$FORMATTED_USER_MESSAGE" | while IFS= read -r line; do
                echo "${COLOR_RESET}｜${MESSAGE_COLOR}${line}${COLOR_RESET}"
            done)
        fi
    fi
fi

# 輸出狀態列
echo -e "${COLOR_RESET}[${MODEL_COLOR}${MODEL_ICON} ${MODEL}${COLOR_RESET}] 📂 $PROJECT_NAME$BRANCH$CONTEXT_USAGE | $TOTAL_HOURS"

# 輸出使用者訊息
[ -n "$USER_MESSAGE_DISPLAY" ] && echo -e "$USER_MESSAGE_DISPLAY"
