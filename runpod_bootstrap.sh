#!/bin/bash
set -e

echo "=================================================================="
echo "🚀 Starting RunPod Pre-Installed ComfyUI Studio Bootstrap (v4)..."
echo "=================================================================="

WORKSPACE="/workspace"
STUDIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -d "/workspace/ComfyUI" ]; then
    COMFY_DIR="/workspace/ComfyUI"
elif [ -d "/workspace/runpod-slim/ComfyUI" ]; then
    COMFY_DIR="/workspace/runpod-slim/ComfyUI"
elif [ -d "/ComfyUI" ]; then
    COMFY_DIR="/ComfyUI"
elif [ -d "/opt/ComfyUI" ]; then
    COMFY_DIR="/opt/ComfyUI"
else
    echo "❌ Could not auto-detect ComfyUI directory! Set COMFY_DIR manually."
    exit 1
fi
echo "✅ Detected existing ComfyUI installation at: $COMFY_DIR"

if [ -f "$COMFY_DIR/venv/bin/pip" ]; then
    PIP_CMD="$COMFY_DIR/venv/bin/pip"
    PY_CMD="$COMFY_DIR/venv/bin/python"
    HF_CLI="$COMFY_DIR/venv/bin/hf"
    STREAMLIT_CMD="$COMFY_DIR/venv/bin/streamlit"
elif [ -f "/workspace/venv/bin/pip" ]; then
    PIP_CMD="/workspace/venv/bin/pip"
    PY_CMD="/workspace/venv/bin/python"
    HF_CLI="/workspace/venv/bin/hf"
    STREAMLIT_CMD="/workspace/venv/bin/streamlit"
else
    PIP_CMD="pip"
    PY_CMD="python"
    HF_CLI="hf"
    STREAMLIT_CMD="streamlit"
fi
echo "✅ Using Python executable: $PY_CMD"

echo "📦 [1/4] Checking Streamlit dependencies..."
$PIP_CMD install -q "huggingface_hub[cli]" streamlit pillow
export HF_XET_HIGH_PERFORMANCE=1

echo "🧩 [2/4] Checking Custom Nodes..."
NODES_DIR="$COMFY_DIR/custom_nodes"
mkdir -p "$NODES_DIR"
cd "$NODES_DIR"

ensure_node() {
    local repo_url=$1
    local dir_name=$(basename "$repo_url" .git)
    if [ -d "$dir_name" ]; then
        echo "   ✔ [$dir_name] already installed — skipping."
    else
        echo "   ➕ Installing missing node: [$dir_name]..."
        git clone --depth 1 "$repo_url"
        if [ -f "$dir_name/requirements.txt" ]; then
            $PIP_CMD install -q -r "$dir_name/requirements.txt" || true
        fi
    fi
}

ensure_node "https://github.com/ltdrdata/ComfyUI-Manager.git"
ensure_node "https://github.com/ltdrdata/ComfyUI-Inspire-Pack.git"
ensure_node "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"
ensure_node "https://github.com/WASasquatch/was-node-suite-comfyui.git"
ensure_node "https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git"

echo "📁 [3/4] Ensuring Directories Exist..."
MODELS_DIR="$COMFY_DIR/models"
mkdir -p "$MODELS_DIR/checkpoints" \
         "$MODELS_DIR/unet" \
         "$MODELS_DIR/clip" \
         "$MODELS_DIR/vae" \
         "$MODELS_DIR/clip_vision" \
         "$MODELS_DIR/loras" \
         "$COMFY_DIR/input" \
         "$COMFY_DIR/output/episodes/ep01/panels" \
         "$COMFY_DIR/output/episodes/ep01/videos"

MODE=${1:-"all"}
echo "⚡ [4/4] Verifying / Pulling Model Weights (Mode: $MODE)..."

if [[ "$MODE" == "all" || "$MODE" == "stage1" ]]; then
    echo "--> Checking Stage 1 (Flux.1 Dev FP8) weights..."
    if [ ! -f "$MODELS_DIR/checkpoints/flux1-dev.safetensors" ] && [ ! -f "$MODELS_DIR/checkpoints/flux1-dev-fp8.safetensors" ]; then
        $HF_CLI download Comfy-Org/flux1-dev flux1-dev-fp8.safetensors --local-dir "$MODELS_DIR/checkpoints"
        ln -sf "$MODELS_DIR/checkpoints/flux1-dev-fp8.safetensors" "$MODELS_DIR/checkpoints/flux1-dev.safetensors"
    else
        echo "   ✔ Flux.1 checkpoint already present."
    fi
fi

if [[ "$MODE" == "all" || "$MODE" == "stage2" ]]; then
    echo "--> Checking Stage 2 (Wan 2.1 I2V 720p FP8) weights..."
    if [ ! -f "$MODELS_DIR/unet/wan2.1_i2v_720p_14B_fp8_e4m3fn.safetensors" ]; then
        $HF_CLI download Comfy-Org/Wan_2.1_ComfyUI_repackaged \
            split_files/diffusion_models/wan2.1_i2v_720p_14B_fp8_e4m3fn.safetensors \
            split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors \
            split_files/vae/wan_2.1_vae.safetensors \
            split_files/clip_vision/clip_vision_h.safetensors \
            --local-dir "$MODELS_DIR/wan_temp"

        mv -f "$MODELS_DIR/wan_temp/split_files/diffusion_models/"*.safetensors "$MODELS_DIR/unet/" 2>/dev/null || true
        mv -f "$MODELS_DIR/wan_temp/split_files/text_encoders/"*.safetensors "$MODELS_DIR/clip/" 2>/dev/null || true
        mv -f "$MODELS_DIR/wan_temp/split_files/vae/"*.safetensors "$MODELS_DIR/vae/" 2>/dev/null || true
        mv -f "$MODELS_DIR/wan_temp/split_files/clip_vision/"*.safetensors "$MODELS_DIR/clip_vision/" 2>/dev/null || true
        rm -rf "$MODELS_DIR/wan_temp"
    else
        echo "   ✔ Wan 2.1 I2V weights already present."
    fi
fi

echo "🎬 Starting Services..."
if pgrep -f "main.py" > /dev/null; then
    pkill -f "main.py" || true
    sleep 2
fi

cd "$COMFY_DIR"
nohup $PY_CMD main.py --listen 0.0.0.0 --port 8188 > "$WORKSPACE/comfyui.log" 2>&1 &
echo "✅ ComfyUI listening on Port 8188"

cd "$STUDIO_DIR"
if [ -f "batch_studio_app_v2.py" ]; then
    pkill -f "streamlit run batch_studio_app_v2.py" 2>/dev/null || true
    nohup $STREAMLIT_CMD run batch_studio_app_v2.py \
        --server.port 8501 \
        --server.address 0.0.0.0 \
        --server.headless true > "$WORKSPACE/streamlit.log" 2>&1 &
    echo "✅ Streamlit Studio listening on Port 8501"
fi

echo "=================================================================="
echo "🎉 Ready! Connect via RunPod HTTP Port 8501 (Studio) & 8188 (ComfyUI)"
echo "=================================================================="
