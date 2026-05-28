#!/bin/bash
# ============================================================================
# NodeODM Auto-Run Script (v3 - Fixed Upload Bug)
# ============================================================================
# Workflow otomatis: input folder → submit → monitor (with spinner) → download
#
# Usage:
#   ./run_odm.sh <input_folder> [output_folder]
#   ./run_odm.sh --resume <uuid> [output_folder]
# ============================================================================

set -e

# === Konfigurasi ===
NODE_HOST="${NODE_HOST:-localhost}"
NODE_PORT="${NODE_PORT:-3000}"
NODE_URL="http://${NODE_HOST}:${NODE_PORT}"

# Opsi ODM untuk wajah / close-up object
# Edit sesuai kebutuhan
ODM_OPTIONS='[
    {"name":"feature-quality","value":"high"},
    {"name":"min-num-features","value":16000},
    {"name":"matcher-type","value":"flann"},
    {"name":"mesh-octree-depth","value":11},
    {"name":"mesh-size","value":200000},
    {"name":"use-3dmesh","value":true},
    {"name":"pc-quality","value":"medium"},
    {"name":"ignore-gsd","value":true},
    {"name":"bg-removal","value":true}
]'

# === Warna ===
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# === Variabel global ===
TASK_UUID=""
START_TIME=""
TMP_DIR=""

# === Helper functions ===

print_header() {
    echo
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${CYAN}║          NodeODM Auto-Run — 3D Reconstruction            ║${NC}"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo
}

print_step() {
    echo
    echo -e "${BOLD}${BLUE}━━━ [${1}/${2}] ${3} ━━━${NC}"
}

log_ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
log_info()  { echo -e "  ${BLUE}ℹ${NC} $1"; }
log_warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
log_err()   { echo -e "  ${RED}✗${NC} $1"; }

fmt_time() {
    local s=$1
    if [ "$s" -ge 3600 ]; then
        printf "%02d:%02d:%02d" $((s/3600)) $((s%3600/60)) $((s%60))
    else
        printf "%02d:%02d" $((s/60)) $((s%60))
    fi
}

spinner_chars='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

draw_progress_bar() {
    local progress=$1
    local width=30
    local filled
    filled=$(awk "BEGIN {printf \"%d\", $progress * $width / 100}")
    local empty=$((width - filled))
    
    printf "${GREEN}"
    for ((i=0; i<filled; i++)); do printf "█"; done
    printf "${DIM}"
    for ((i=0; i<empty; i++)); do printf "░"; done
    printf "${NC}"
}

cleanup_int() {
    tput cnorm 2>/dev/null || true
    echo
    echo
    if [ -n "$TASK_UUID" ]; then
        log_warn "Script interrupted. Task is still running on server."
        log_info "Task UUID: ${BOLD}$TASK_UUID${NC}"
        log_info "To resume monitoring, run:"
        echo "    $(basename "$0") --resume $TASK_UUID"
    fi
    [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ] && rm -rf "$TMP_DIR"
    exit 130
}
trap cleanup_int INT TERM

cleanup_exit() {
    tput cnorm 2>/dev/null || true
    [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ] && rm -rf "$TMP_DIR"
}
trap cleanup_exit EXIT

# === FIX: Spinner untuk operasi background (gantikan curl -#) ===
# Usage: run_with_spinner "Message" command args...
run_with_spinner() {
    local msg="$1"
    shift
    
    # Disable set -e sementara biar exit code bisa di-check
    set +e
    "$@" &
    local pid=$!
    set -e
    
    tput civis 2>/dev/null || true
    
    local spinner_idx=0
    while kill -0 "$pid" 2>/dev/null; do
        local spin_char="${spinner_chars:$spinner_idx:1}"
        printf "\r  ${MAGENTA}%s${NC}  %s   " "$spin_char" "$msg"
        spinner_idx=$(((spinner_idx + 1) % ${#spinner_chars}))
        sleep 0.1
    done
    
    set +e
    wait "$pid"
    local exit_code=$?
    set -e
    
    tput cnorm 2>/dev/null || true
    
    # Clear spinner line
    printf "\r%-80s\r" " "
    
    return $exit_code
}

# === Validation ===

check_dependencies() {
    local missing=()
    for cmd in curl jq unzip; do
        command -v $cmd &> /dev/null || missing+=("$cmd")
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        log_err "Missing required commands: ${missing[*]}"
        log_info "Install with: sudo apt install ${missing[*]}"
        exit 1
    fi
}

check_node_connection() {
    if ! curl -s --max-time 5 "$NODE_URL/info" > /dev/null; then
        log_err "Cannot connect to NodeODM at ${NODE_URL}"
        log_info "Make sure container is running:"
        echo "    sudo docker ps"
        echo "    sudo docker start nodeodm"
        exit 1
    fi
    
    local info version engine mem_total mem_avail cores
    info=$(curl -s "$NODE_URL/info")
    version=$(echo "$info" | jq -r '.version')
    engine=$(echo "$info" | jq -r '.engineVersion')
    mem_total=$(echo "$info" | jq -r '.totalMemory')
    mem_avail=$(echo "$info" | jq -r '.availableMemory')
    cores=$(echo "$info" | jq -r '.cpuCores')
    
    local mem_total_gb mem_avail_gb
    mem_total_gb=$(awk "BEGIN {printf \"%.1f\", $mem_total/1024/1024/1024}")
    mem_avail_gb=$(awk "BEGIN {printf \"%.1f\", $mem_avail/1024/1024/1024}")
    
    log_ok "Connected to NodeODM ${BOLD}v${version}${NC} (engine ${engine})"
    log_info "Server: ${cores} cores, ${mem_avail_gb}/${mem_total_gb} GB RAM available"
}

scan_images() {
    local dir="$1"
    
    if [ ! -d "$dir" ]; then
        log_err "Directory not found: $dir"
        exit 1
    fi
    
    IMAGES=()
    while IFS= read -r -d '' f; do
        IMAGES+=("$f")
    done < <(find "$dir" -maxdepth 1 -type f \
        \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \
           -o -iname "*.tif" -o -iname "*.tiff" \) \
        -print0 | sort -z)
    
    IMAGE_COUNT=${#IMAGES[@]}
    
    if [ "$IMAGE_COUNT" -lt 5 ]; then
        log_err "Need at least 5 images for SfM reconstruction."
        log_err "Found only $IMAGE_COUNT images in: $dir"
        exit 1
    fi
    
    local total_size=0
    for img in "${IMAGES[@]}"; do
        local sz
        sz=$(stat -c%s "$img" 2>/dev/null || stat -f%z "$img" 2>/dev/null)
        total_size=$((total_size + sz))
    done
    local size_mb
    size_mb=$(awk "BEGIN {printf \"%.1f\", $total_size/1024/1024}")
    
    log_ok "Found ${BOLD}${IMAGE_COUNT}${NC} images (${size_mb} MB total)"
    
    if [ "$IMAGE_COUNT" -lt 20 ]; then
        log_warn "Few images detected. SfM works best with 30+ overlapping images."
    elif [ "$IMAGE_COUNT" -gt 200 ]; then
        log_warn "Many images. Processing may take very long on limited hardware."
    fi
}

# === FIXED: Submit task — gantikan curl -# dengan curl silent + spinner ===
submit_task() {
    local task_name="$1"
    
    log_info "Uploading $IMAGE_COUNT images and submitting task..."
    log_info "Task name: ${BOLD}$task_name${NC}"
    echo
    
    # Setup temp dir untuk response & error
    TMP_DIR=$(mktemp -d)
    local response_file="$TMP_DIR/response.json"
    local http_code_file="$TMP_DIR/http_code"
    local error_file="$TMP_DIR/error.log"
    
    # Build curl form args
    local form_args=()
    for img in "${IMAGES[@]}"; do
        form_args+=(-F "images=@$img")
    done
    form_args+=(-F "name=$task_name")
    form_args+=(-F "options=$ODM_OPTIONS")
    
    # Upload function — dijalankan di background oleh run_with_spinner
    do_upload() {
        # -s: silent (NO progress bar — ini yang dulu bikin bug)
        # -o: save response body ke file
        # -w: write http code ke stdout
        curl -s \
            -o "$response_file" \
            -w "%{http_code}" \
            -X POST \
            "${form_args[@]}" \
            "$NODE_URL/task/new" \
            > "$http_code_file" \
            2> "$error_file"
    }
    
    if ! run_with_spinner "Uploading & submitting task..." do_upload; then
        log_err "Upload failed (curl error)."
        if [ -s "$error_file" ]; then
            echo "Error log:"
            cat "$error_file"
        fi
        exit 1
    fi
    
    # Cek HTTP status code
    local http_code
    http_code=$(cat "$http_code_file" 2>/dev/null || echo "")
    
    if [ "$http_code" != "200" ]; then
        log_err "Server returned HTTP $http_code"
        if [ -s "$response_file" ]; then
            echo "Response body:"
            cat "$response_file"
            echo
        fi
        exit 1
    fi
    
    # Parse UUID dari response file
    if [ ! -s "$response_file" ]; then
        log_err "Empty response from server."
        exit 1
    fi
    
    TASK_UUID=$(jq -r '.uuid // empty' "$response_file" 2>/dev/null || echo "")
    
    if [ -z "$TASK_UUID" ]; then
        log_err "Failed to parse task UUID from response."
        echo "Response content:"
        cat "$response_file"
        echo
        local server_err
        server_err=$(jq -r '.error // empty' "$response_file" 2>/dev/null || echo "")
        [ -n "$server_err" ] && log_err "Server error: $server_err"
        exit 1
    fi
    
    # Simpan UUID untuk recovery
    echo "$TASK_UUID" > .last_task_uuid
    
    log_ok "Task submitted!"
    log_info "UUID: ${BOLD}$TASK_UUID${NC}"
    log_info "Saved to: .last_task_uuid"
}

monitor_task() {
    log_info "Monitoring task progress..."
    log_info "${DIM}(Press Ctrl+C to detach — task continues running)${NC}"
    echo
    
    START_TIME=$(date +%s)
    local spinner_idx=0
    
    tput civis 2>/dev/null || true
    
    while true; do
        local info status_code progress
        info=$(curl -s --max-time 10 "$NODE_URL/task/$TASK_UUID/info" 2>/dev/null || echo "{}")
        status_code=$(echo "$info" | jq -r '.status.code // "?"')
        progress=$(echo "$info" | jq -r '.progress // 0')
        
        local elapsed=$(($(date +%s) - START_TIME))
        local elapsed_str
        elapsed_str=$(fmt_time $elapsed)
        
        local spin_char="${spinner_chars:$spinner_idx:1}"
        spinner_idx=$(((spinner_idx + 1) % ${#spinner_chars}))
        
        local status_text status_color
        case "$status_code" in
            10) status_text="QUEUED   "; status_color="$YELLOW" ;;
            20) status_text="RUNNING  "; status_color="$BLUE" ;;
            30) status_text="FAILED   "; status_color="$RED" ;;
            40) status_text="COMPLETED"; status_color="$GREEN" ;;
            50) status_text="CANCELED "; status_color="$YELLOW" ;;
            *)  status_text="UNKNOWN  "; status_color="$NC" ;;
        esac
        
        printf "\r  ${MAGENTA}%s${NC}  ${status_color}%s${NC}  " "$spin_char" "$status_text"
        draw_progress_bar "$progress"
        printf "  ${BOLD}%5.1f%%${NC}  ${DIM}elapsed:${NC} %s   " "$progress" "$elapsed_str"
        
        case "$status_code" in
            40)
                tput cnorm 2>/dev/null || true
                echo; echo
                log_ok "Task ${GREEN}${BOLD}COMPLETED${NC} successfully in $elapsed_str!"
                return 0
                ;;
            30)
                tput cnorm 2>/dev/null || true
                echo; echo
                local err
                err=$(echo "$info" | jq -r '.status.errorMessage // "Unknown error"')
                log_err "Task ${RED}${BOLD}FAILED${NC}: $err"
                echo
                log_info "To see full log, run:"
                echo "    curl -s $NODE_URL/task/$TASK_UUID/output | jq -r '.[]'"
                return 1
                ;;
            50)
                tput cnorm 2>/dev/null || true
                echo; echo
                log_warn "Task was ${YELLOW}${BOLD}CANCELED${NC}."
                return 1
                ;;
        esac
        
        sleep 1
    done
}

# === FIXED: Download — gantikan curl -# dengan curl silent + spinner ===
download_results() {
    local output_dir="$1"
    
    mkdir -p "$output_dir"
    
    local zip_path="$output_dir/all.zip"
    log_info "Downloading results to ${BOLD}$output_dir/${NC}"
    echo
    
    do_download() {
        curl -s -o "$zip_path" "$NODE_URL/task/$TASK_UUID/download/all.zip"
    }
    
    if ! run_with_spinner "Downloading all.zip..." do_download; then
        log_err "Download failed."
        exit 1
    fi
    
    if [ ! -s "$zip_path" ]; then
        log_err "Downloaded file is empty."
        exit 1
    fi
    
    local zip_size
    zip_size=$(du -h "$zip_path" | cut -f1)
    log_ok "Downloaded: ${BOLD}all.zip${NC} (${zip_size})"
    
    do_extract() {
        unzip -q -o "$zip_path" -d "$output_dir"
    }
    
    if ! run_with_spinner "Extracting archive..." do_extract; then
        log_err "Extraction failed."
        exit 1
    fi
    
    log_ok "Extracted to: ${BOLD}$output_dir/${NC}"
    
    rm -f "$zip_path"
}

show_summary() {
    local output_dir="$1"
    local total_time=$(($(date +%s) - START_TIME))
    
    echo
    echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${GREEN}║                    🎉 ALL DONE!                          ║${NC}"
    echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo
    
    log_info "Total time: ${BOLD}$(fmt_time $total_time)${NC}"
    log_info "Output folder: ${BOLD}$output_dir/${NC}"
    echo
    
    echo -e "  ${BOLD}Key output files:${NC}"
    local mesh="$output_dir/odm_texturing/odm_textured_model_geo.obj"
    local pc_laz="$output_dir/odm_georeferencing/odm_georeferenced_model.laz"
    local pc_ply="$output_dir/odm_georeferencing/odm_georeferenced_model.ply"
    local ortho="$output_dir/odm_orthophoto/odm_orthophoto.tif"
    local report="$output_dir/report/report.pdf"
    local cameras="$output_dir/cameras.json"
    
    [ -f "$mesh" ]    && echo -e "    ${GREEN}●${NC} 3D Mesh    : $mesh"
    [ -f "$pc_laz" ]  && echo -e "    ${GREEN}●${NC} Point Cloud: $pc_laz"
    [ -f "$pc_ply" ]  && echo -e "    ${GREEN}●${NC} Point Cloud: $pc_ply"
    [ -f "$ortho" ]   && echo -e "    ${GREEN}●${NC} Orthophoto : $ortho"
    [ -f "$cameras" ] && echo -e "    ${GREEN}●${NC} Cameras    : $cameras"
    [ -f "$report" ]  && echo -e "    ${GREEN}●${NC} Report     : $report"
    
    echo
    local total_output_size
    total_output_size=$(du -sh "$output_dir" 2>/dev/null | cut -f1)
    log_info "Total output size: ${BOLD}$total_output_size${NC}"
    
    if [ -f "$mesh" ]; then
        echo
        echo -e "  ${BOLD}To view the 3D model:${NC}"
        echo -e "    ${CYAN}meshlab '$mesh'${NC}"
    fi
    
    if [ -f "$report" ]; then
        echo -e "  ${BOLD}To view the processing report:${NC}"
        echo -e "    ${CYAN}xdg-open '$report'${NC}"
    fi
    
    echo
}

usage() {
    cat <<EOF
${BOLD}NodeODM Auto-Run${NC} — automated 3D reconstruction pipeline

${BOLD}Usage:${NC}
  $(basename "$0") <input_folder> [output_folder]
  $(basename "$0") --resume <uuid> [output_folder]

${BOLD}Arguments:${NC}
  input_folder    Folder containing images (.jpg/.png/.tif)
  output_folder   Where to save results (default: ./output_<timestamp>)

${BOLD}Options:${NC}
  --resume UUID   Resume monitoring/downloading an existing task
  --help          Show this help

${BOLD}Environment variables:${NC}
  NODE_HOST       NodeODM host (default: localhost)
  NODE_PORT       NodeODM port (default: 3000)

${BOLD}Examples:${NC}
  $(basename "$0") ./dataset_frames
  $(basename "$0") ./dataset_frames ./hasil_3d
  $(basename "$0") --resume f5d7ee24-b322-4a6c-9c0f-d40abbfe46fc

EOF
}

main() {
    if [ $# -eq 0 ] || [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        usage
        exit 0
    fi
    
    print_header
    
    print_step 1 4 "Pre-flight checks"
    check_dependencies
    log_ok "Dependencies OK (curl, jq, unzip)"
    check_node_connection
    
    if [ "$1" = "--resume" ]; then
        TASK_UUID="$2"
        [ -z "$TASK_UUID" ] && { log_err "UUID required for --resume"; exit 1; }
        
        local output_dir="${3:-./output_${TASK_UUID:0:8}}"
        
        log_info "Resuming task: ${BOLD}$TASK_UUID${NC}"
        
        print_step 2 4 "Resume monitoring"
        local info status_code
        info=$(curl -s "$NODE_URL/task/$TASK_UUID/info")
        status_code=$(echo "$info" | jq -r '.status.code // "?"')
        
        case "$status_code" in
            10|20)
                monitor_task || exit 1
                ;;
            40)
                log_ok "Task already completed. Skipping to download."
                START_TIME=$(date +%s)
                ;;
            30)
                local err
                err=$(echo "$info" | jq -r '.status.errorMessage')
                log_err "Task already failed: $err"
                exit 1
                ;;
            *)
                log_err "Task in unknown state: $status_code"
                exit 1
                ;;
        esac
        
        print_step 3 4 "Download results"
        download_results "$output_dir"
        
        print_step 4 4 "Summary"
        show_summary "$output_dir"
        return
    fi
    
    local input_dir="$1"
    local output_dir="${2:-./output_$(date +%Y%m%d_%H%M%S)}"
    local task_name
    task_name="task_$(basename "$input_dir")_$(date +%H%M%S)"
    
    log_ok "Input folder: ${BOLD}$input_dir${NC}"
    log_ok "Output folder: ${BOLD}$output_dir${NC}"
    
    scan_images "$input_dir"
    
    print_step 2 4 "Submitting task"
    submit_task "$task_name"
    
    print_step 3 4 "Processing (this may take a while)"
    if ! monitor_task; then
        log_warn "Task did not complete. Skipping download."
        exit 1
    fi
    
    print_step 4 4 "Downloading results"
    download_results "$output_dir"
    
    show_summary "$output_dir"
}

main "$@"