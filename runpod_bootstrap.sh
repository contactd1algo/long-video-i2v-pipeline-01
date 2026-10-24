#!/bin/bash
set -e

echo "===================================================="
echo "🚀 Starting Ephemeral RunPod Studio Bootstrap..."
echo "===================================================="

WORKSPACE="/workspace"
COMFY_DIR="$WORKSPACE/ComfyUI"
STUDIO_DIR="$WORKSPACE/studio"
mkdir -p "$STUDIO_DIR"

# 1. Install System Dependencies & Fast Downloaders
echo "📦 [1/6] Installing system tools & Python packages..."
apt-get update -y && apt-get install -y aria2 ffmpeg git git-lfs wget curl unzip
pip install -q --upgrade pip
pip install -q "huggingface_hub[cli]" hf_transfer streamlit pillow insightface onnxruntime-gpu

# Enable ultra-fast multi-threaded HuggingFace downloads (up to 500MB/s+ on RunPod)
export HF_HUB_ENABLE_HF_TRANSFER=1

# 2. Clone or Locate ComfyUI
echo "🔧 [2/6] Setting up ComfyUI..."
if [ ! -d "$COMFY_DIR" ]; then
    git clone https://github.com/comfyanonymous/ComfyUI.git "$COMFY_DIR"
    pip install -q -r "$COMFY_DIR/requirements.txt"
fi

# 3. Clone Required Custom Nodes in Parallel
echo "🧩 [3/6] Installing Custom Nodes for Workflows 1 & 2..."
NODES_DIR="$COMFY_DIR/custom_nodes"
cd "$NODES_DIR"

clone_node() {
    local repo_url=$1
    local dir_name=$(basename "$repo_url" .git)
    if [ ! -d "$dir_name" ]; then
        git clone --depth 1 "$repo_url"
        if [ -f "$dir_name/requirements.txt" ]; then
            pip install -q -r "$dir_name/requirements.txt" || true
        fi
    fi
}

clone_node "https://github.com/ltdrdata/ComfyUI-Manager.git"
clone_node "https://github.com/balazik/ComfyUI-PuLID-Flux.git"
clone_node "https://github.com/ltdrdata/ComfyUI-Inspire-Pack.git"
clone_node "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"
clone_node "https://github.com/WASasquatch/was-node-suite-comfyui.git"
clone_node "https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git"

# 4. Create Model Directories
echo "📁 [4/6] Preparing Model Directories..."
MODELS_DIR="$COMFY_DIR/models"
mkdir -p "$MODELS_DIR/checkpoints" \
         "$MODELS_DIR/unet" \
         "$MODELS_DIR/clip" \
         "$MODELS_DIR/vae" \
         "$MODELS_DIR/clip_vision" \
         "$MODELS_DIR/loras" \
         "$MODELS_DIR/pulid" \
         "$MODELS_DIR/insightface/models/antelopev2" \
         "$COMFY_DIR/input" \
         "$COMFY_DIR/output/episodes/ep01/panels" \
         "$COMFY_DIR/output/episodes/ep01/videos"

# 5. High-Speed Model Weights Download (Toggle Mode: "all", "stage1", "stage2")
MODE=${1:-"all"}
echo "⚡ [5/6] Downloading Models (Selected Mode: $MODE)..."

# Note: If downloading gated Flux-dev, export HF_TOKEN="hf_xxx" before running
if [[ "$MODE" == "all" || "$MODE" == "stage1" ]]; then
    echo "--> Pulling Stage 1 (Flux.1 + PuLID + InsightFace) weights..."
    # PuLID-Flux weight
    huggingface-cli download guozinan/PuLID pulid_flux_v0.9.1.safetensors --local-dir "$MODELS_DIR/pulid"
    
    # InsightFace AntelopeV2 for PuLID
    if [ ! -f "$MODELS_DIR/insightface/models/antelopev2/glintr100.onnx" ]; then
        aria2c -x 16 -s 16 -d "$MODELS_DIR/insightface/models" -o antelopev2.zip \
            "https://huggingface.co/MonsterMMORPG/tools/resolve/main/antelopev2.zip"
        unzip -q -o "$MODELS_DIR/insightface/models/antelopev2.zip" -d "$MODELS_DIR/insightface/models/antelopev2/"
    fi

    # Flux FP8 Single Checkpoint (Fits comfortably on 24GB VRAM alongside PuLID)
    huggingface-cli download Comfy-Org/flux1-dev flux1-dev-fp8.safetensors --local-dir "$MODELS_DIR/checkpoints"
    ln -sf "$MODELS_DIR/checkpoints/flux1-dev-fp8.safetensors" "$MODELS_DIR/checkpoints/flux1-dev.safetensors"
fi

if [[ "$MODE" == "all" || "$MODE" == "stage2" ]]; then
    echo "--> Pulling Stage 2 (Wan 2.1 I2V 720p FP8) weights..."
    huggingface-cli download Comfy-Org/Wan_2.1_ComfyUI_repackaged \
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
fi

# 6. Launch ComfyUI & Streamlit Studio on Exposed RunPod Ports
echo "🎬 [6/6] Starting Services..."
cd "$COMFY_DIR"
nohup python main.py --listen 0.0.0.0 --port 8188 > "$WORKSPACE/comfyui.log" 2>&1 &

cd "$STUDIO_DIR"
if [ -f "batch_studio_app_v2.py" ]; then
    nohup streamlit run batch_studio_app_v2.py \
        --server.port 8501 \
        --server.address 0.0.0.0 \
        --server.headless true > "$WORKSPACE/streamlit.log" 2>&1 &
    echo "✅ Streamlit Studio running on Port 8501!"
else
    echo "⚠️ Place batch_studio_app_v2.py and workflow JSONs in $STUDIO_DIR and run streamlit."
fi

echo "===================================================="
echo "✅ Bootstrap Complete!"
echo "   - ComfyUI Log: tail -f /workspace/comfyui.log"
echo "   - Studio Log:  tail -f /workspace/streamlit.log"
echo "===================================================="
