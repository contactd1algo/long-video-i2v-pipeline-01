# 🎬 Comic-to-Video AI Production Studio

A high-performance, CLI-driven, 2-stage AI video generation and audio muxing pipeline optimized for enterprise hardware (**NVIDIA RTX PRO 6000 Blackwell, 96GB+ VRAM**). 

This system transforms a JSON storyboard manifest into a fully polished, multi-shot animated episode featuring clean 2D cel-shaded comic panels, smooth image-to-video motion, neural voiceover, Foley sound effects, background music, and automatic FFmpeg master stitching.

---

## 🏗️ Project Architecture & Directory Hierarchy

The pipeline organizes productions into clean, reproducible project folders:

```text
/workspace/runpod-slim/ComfyUI/output/projects/
└── Arjun/
    ├── project_config.json          # Locks style anchors, character identity, & run states
    ├── Arjun_COMPLETE_SERIES.mp4    # Auto-stitched master movie across multiple runs
    ├── bgm.mp3 (optional)           # Background music track mixed across all runs
    └── run1/
        ├── storyboard_manifest.json
        ├── panels/                  # Stage 1 static comic panels (PNG)
        ├── videos/                  # Stage 2 raw I2V video clips (MP4)
        ├── audio/                   # Stage 3 TTS voiceover & Foley audio tracks
        └── final/
            ├── Arjun_run1_master.mp4    # Final stitched run video with audio & BGM
            └── Arjun_run1_last_frame.png # Auto-extracted final frame for Run 2 continuity
    └── run2/
```

---

## 🚀 Quickstart Guide

### 1. Bootstrap the Pod & Environment
Clone your repo and run the bootstrap script to install dependencies (`ffmpeg`, `edge-tts`, `opencv-python`), download model weights & speed LoRAs, and launch ComfyUI in **GPU-Resident Mode** (`--gpu-only --disable-dynamic-vram`):
```bash
cd /workspace/runpod-slim/long-video-i2v-pipeline-01
chmod +x runpod_bootstrap.sh
./runpod_bootstrap.sh
```

### 2. Launch the Production Studio
Run the unified CLI interface:
```bash
python run_cli_studio.py
```

### 3. CLI Menu Options
* **Option 1:** Run Full Automated Pipeline (Stage 1 Panels $ightarrow$ Stage 2 Videos $ightarrow$ Stage 3 Audio & Master Stitch).
* **Option 2:** Stage 1 Only (Generate Clean Comic Panels).
* **Option 3:** Stage 2 Only (Generate Sequential I2V Videos using Wan 2.1 + 4-Step Speed LoRA).
* **Option 4:** Stage 3 Only (Generate Voiceover/SFX, Mix BGM, and Stitch Master Video).
* **Option 5:** Switch Project Name or Advance to Next Run (`run1` $ightarrow$ `run2`).
* **Option 6:** Speed & Continuity Settings (Toggle 720p/480p, Sampling Steps, Shot Chaining).
* **Option 7:** Exit.

---

## 🎵 Adding Background Music (BGM)
To add background music throughout your episode:
1. Place any `.mp3` or `.wav` music file directly inside your project folder:
   `/workspace/runpod-slim/ComfyUI/output/projects/Arjun/bgm.mp3`
2. Run **Stage 3** (or Option 1). Stage 3 will automatically loop and duck the background music underneath your dialogue and Foley sound effects at a professional cinematic volume.

---

## 💡 Pro-Tips for Fixing Robotic Motion (Walking/Action)
* **The Challenge:** Video diffusion models (like Wan 2.1) can sometimes render full-body locomotion (like walking across a room) with slight foot sliding or rigidity if the clip duration is short (5 seconds).
* **The Fix:** 
  1. Focus manifest actions on **medium close-ups, over-the-shoulder angles, upper-body interactions, and subtle camera push-ins/pans** rather than wide-shot walking.
  2. In your `motion_prompt`, use phrases like `"subtle camera push-in, micro-expressions, character shifting weight, blinking eyes"` rather than `"running across the corridor"`.
  3. Use **Img2Img Refinement (Stage 1.5)** or **Intra-run Chaining** to lock anatomical proportions between cuts.
