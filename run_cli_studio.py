import os
import sys
import json
import time
import glob
import math
import wave
import struct
import shutil
import subprocess
import urllib.request
import urllib.error

COMFYUI_HOST = "http://127.0.0.1:8188"

def detect_comfy_dir():
    for candidate in ["/workspace/runpod-slim/ComfyUI", "/workspace/ComfyUI", "/ComfyUI", "/opt/ComfyUI"]:
        if os.path.isdir(candidate):
            return candidate
    return "/workspace/runpod-slim/ComfyUI"

COMFYUI_DIR = detect_comfy_dir()
COMFYUI_OUTPUT_DIR = os.path.join(COMFYUI_DIR, "output")
COMFYUI_INPUT_DIR = os.path.join(COMFYUI_DIR, "input")

WF1_PATH = "workflow1_panel_generator_and_tweaker.json"
WF2_PATH = "workflow2_sequential_i2v_wan21.json"

DEFAULT_STYLE_PREFIX = (
    "clean 2D graphic novel illustration, modern comic art style, crisp ink lineart, "
    "flat vibrant cel-shading, bright studio lighting, well-lit scene, vivid colors, "
    "high visibility, 16:9 cinematic framing, no borders, uncropped"
)
DEFAULT_CHAR_ANCHOR = (
    "Arjun, 30yo male engineer, short black hair, light beard, "
    "dark olive tactical jacket over charcoal crewneck"
)

# ==============================================================================
# PROJECT -> RUN -> SHOT HIERARCHY MANAGER
# ==============================================================================
class StudioContext:
    def __init__(self, project_name="Arjun", run_id=1):
        self.project_name = project_name
        self.run_id = int(run_id)
        self.style_prefix = DEFAULT_STYLE_PREFIX
        self.char_anchor = DEFAULT_CHAR_ANCHOR
        self.wan_steps = 15          # Fast default (15 steps vs 30)
        self.wan_resolution = "720p" # "720p" (1280x720) or "480p" (832x480 for 3x speed)
        self.use_speed_lora = True   # Auto-use 4-step LightX2V LoRA if present
        self.chain_within_run = False # If True, every shot N+1 uses last frame of shot N
        self.load_or_init_project()

    @property
    def project_dir(self):
        return os.path.join(COMFYUI_OUTPUT_DIR, "projects", self.project_name)

    @property
    def project_meta_path(self):
        return os.path.join(self.project_dir, "project_config.json")

    @property
    def run_name(self):
        return f"run{self.run_id}"

    @property
    def run_dir(self):
        return os.path.join(self.project_dir, self.run_name)

    @property
    def panels_dir(self):
        return os.path.join(self.run_dir, "panels")

    @property
    def videos_dir(self):
        return os.path.join(self.run_dir, "videos")

    @property
    def audio_dir(self):
        return os.path.join(self.run_dir, "audio")

    @property
    def final_dir(self):
        return os.path.join(self.run_dir, "final")

    def shot_tag(self, shot_idx):
        return f"{self.project_name}_run{self.run_id}_shot{shot_idx}"

    def load_or_init_project(self):
        os.makedirs(self.panels_dir, exist_ok=True)
        os.makedirs(self.videos_dir, exist_ok=True)
        os.makedirs(self.audio_dir, exist_ok=True)
        os.makedirs(self.final_dir, exist_ok=True)
        if os.path.exists(self.project_meta_path):
            try:
                with open(self.project_meta_path, "r") as f:
                    meta = json.load(f)
                self.style_prefix = meta.get("style_prefix", DEFAULT_STYLE_PREFIX)
                self.char_anchor = meta.get("char_anchor", DEFAULT_CHAR_ANCHOR)
            except Exception:
                pass
        else:
            self.save_project_meta()

    def save_project_meta(self, last_frame_path=None):
        meta = {
            "project_name": self.project_name,
            "style_prefix": self.style_prefix,
            "char_anchor": self.char_anchor,
            "latest_run_id": self.run_id,
            "last_frame_path": last_frame_path
        }
        with open(self.project_meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    def get_previous_run_last_frame(self):
        if self.run_id <= 1:
            return None
        prev_run_dir = os.path.join(self.project_dir, f"run{self.run_id - 1}")
        prev_last_frame = os.path.join(prev_run_dir, "final", f"{self.project_name}_run{self.run_id - 1}_last_frame.png")
        if os.path.exists(prev_last_frame):
            return prev_last_frame
        # Or extract on the fly from the last video of run_{N-1}
        prev_videos = sorted(glob.glob(os.path.join(prev_run_dir, "videos", "*.mp4")))
        if prev_videos:
            os.makedirs(os.path.join(prev_run_dir, "final"), exist_ok=True)
            if extract_last_frame(prev_videos[-1], prev_last_frame):
                return prev_last_frame
        return None

# ==============================================================================
# COMFYUI API & QUEUE MONITOR
# ==============================================================================
def queue_prompt(workflow_dict):
    url = f"{COMFYUI_HOST}/prompt"
    payload = json.dumps({"prompt": workflow_dict}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"❌ ComfyUI HTTP Error {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f"❌ Connection error to ComfyUI at {COMFYUI_HOST}: {e}")
        return None

def wait_for_queue_to_finish(poll_interval=2.0):
    print("⏳ Waiting for ComfyUI queue to complete rendering...")
    start_t = time.time()
    while True:
        try:
            with urllib.request.urlopen(f"{COMFYUI_HOST}/queue") as resp:
                q_data = json.loads(resp.read().decode("utf-8"))
            running = len(q_data.get("queue_running", []))
            pending = len(q_data.get("queue_pending", []))
            elapsed = int(time.time() - start_t)
            sys.stdout.write(f"\r   🔄 Active: {running} | Pending: {pending} | Elapsed: {elapsed}s   ")
            sys.stdout.flush()
            if running == 0 and pending == 0:
                print(f"\n✅ ComfyUI queue finished in {elapsed}s!")
                break
        except Exception:
            pass
        time.sleep(poll_interval)

def get_available_loras():
    lora_dir = os.path.join(COMFYUI_DIR, "models", "loras")
    if not os.path.isdir(lora_dir):
        return []
    return [
        f for f in os.listdir(lora_dir)
        if f.endswith(".safetensors") and os.path.getsize(os.path.join(lora_dir, f)) > 1_000_000
    ]

# ==============================================================================
# FRAME EXTRACTION (CONTINUITY ACROSS RUNS & SHOTS)
# ==============================================================================
def extract_last_frame(video_path, output_png_path):
    if not os.path.exists(video_path):
        return False
    # Try OpenCV first
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total_frames - 1))
            ret, frame = cap.read()
            if ret:
                cv2.imwrite(output_png_path, frame)
                cap.release()
                return True
        cap.release()
    except Exception:
        pass
    # Fallback to ffmpeg
    cmd = [
        "ffmpeg", "-y", "-sseof", "-0.1", "-i", video_path,
        "-update", "1", "-q:v", "1", output_png_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.exists(output_png_path)

# ==============================================================================
# AUDIO ENGINE: DIALOGUE TTS + PROCEDURAL FOLEY SFX + FFMPEG MIXING
# ==============================================================================
def generate_Procedural_sfx_wav(sfx_type, output_wav, duration=5.0, sample_rate=24000):
    n_samples = int(duration * sample_rate)
    with wave.open(output_wav, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            t = i / sample_rate
            if sfx_type == "alarm":
                # Urgent pulsing klaxon + low drone
                mod = 1.0 if (int(t * 2.5) % 2 == 0) else 0.25
                val = 0.22 * mod * math.sin(2 * math.pi * 660 * t) + 0.12 * math.sin(2 * math.pi * 110 * t)
            elif sfx_type == "power_down":
                # Descending frequency sweep
                freq = max(45.0, 320.0 * math.exp(-0.55 * t))
                val = 0.28 * math.sin(2 * math.pi * freq * t)
            elif sfx_type == "door_creak":
                # Metallic low groaning resonance
                val = 0.20 * math.sin(2 * math.pi * (95 + 15 * math.sin(2 * math.pi * 1.5 * t)) * t)
            elif sfx_type == "beeps":
                beep = 1.0 if (0.1 < (t % 0.6) < 0.22) else 0.0
                val = 0.18 * beep * math.sin(2 * math.pi * 1200 * t) + 0.08 * math.sin(2 * math.pi * 60 * t)
            else:
                # Default: industrial server room bass drone (60Hz + 120Hz harmonic)
                val = 0.14 * math.sin(2 * math.pi * 60 * t) + 0.07 * math.sin(2 * math.pi * 120 * t)
            sample = max(-32767, min(32767, int(val * 32767)))
            frames.extend(struct.pack("<h", sample))
        wav_file.writeframes(frames)

def generate_shot_audio(ctx, shot):
    idx = shot.get("shot_index", 1)
    tag = ctx.shot_tag(idx)
    dialogue = shot.get("dialogue", "").strip()
    voice = shot.get("voice", "en-US-ChristopherNeural")
    sfx_type = shot.get("sfx_type", "server_hum")

    sfx_wav = os.path.join(ctx.audio_dir, f"{tag}_sfx.wav")
    vo_mp3 = os.path.join(ctx.audio_dir, f"{tag}_vo.mp3")
    mixed_aac = os.path.join(ctx.audio_dir, f"{tag}_mix.m4a")

    # 1. Generate ambient/foley backing track (5.0s)
    generate_Procedural_sfx_wav(sfx_type, sfx_wav, duration=5.0)

    # 2. Generate Neural TTS voiceover if dialogue exists
    has_vo = False
    if dialogue:
        cmd = ["edge-tts", "--voice", voice, "--text", dialogue, "--write-media", vo_mp3]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(vo_mp3) and os.path.getsize(vo_mp3) > 500:
            has_vo = True

    # 3. Mix VO + SFX into a clean 5-second audio stream using ffmpeg
    if has_vo:
        mix_cmd = [
            "ffmpeg", "-y",
            "-i", sfx_wav,
            "-i", vo_mp3,
            "-filter_complex", "[0:a]volume=0.35[sfx];[1:a]adelay=250|250,volume=1.4[vo];[sfx][vo]amix=inputs=2:duration=first:dropout_transition=1[aout]",
            "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-t", "5.0", mixed_aac
        ]
    else:
        mix_cmd = [
            "ffmpeg", "-y", "-i", sfx_wav,
            "-c:a", "aac", "-b:a", "192k", "-t", "5.0", mixed_aac
        ]
    subprocess.run(mix_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return mixed_aac if os.path.exists(mixed_aac) else None

# ==============================================================================
# STAGE 1: PANEL GENERATION (WITH AUTO RUN-TO-RUN LAST FRAME CONTINUITY)
# ==============================================================================
def get_manifest(ctx):
    run_manifest = os.path.join(ctx.run_dir, "storyboard_manifest.json")
    for path in [run_manifest, "storyboard_manifest.json", "sample_storyboard_manifest.json"]:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            # Save a copy inside the run folder for reproducibility
            if path != run_manifest:
                with open(run_manifest, "w") as out_f:
                    json.dump(data, out_f, indent=2)
            return data
    return []

def sanitize_wf1(wf1, is_img2img=False):
    if "2" in wf1:
        lora_name = wf1["2"].get("inputs", {}).get("lora_name", "")
        if lora_name not in get_available_loras():
            wf1.pop("2", None)
            if "3" in wf1:
                wf1["3"]["inputs"]["model"] = ["1", 0]
            if "6" in wf1:
                wf1["6"]["inputs"]["clip"] = ["1", 1]
            if "7" in wf1:
                wf1["7"]["inputs"]["clip"] = ["1", 1]
    if "3" in wf1:
        wf1["3"]["inputs"]["cfg"] = 1.0
    if not is_img2img:
        wf1.pop("30", None)
        wf1.pop("31", None)
    return wf1

def stage1_generate_panels(ctx, wait_for_completion=True):
    manifest = get_manifest(ctx)
    if not manifest:
        print("❌ No manifest found!")
        return

    print(f"\n🚀 [STAGE 1] Project: {ctx.project_name} | Run: {ctx.run_id} | Generating {len(manifest)} panels...")

    # Check if a previous run exists for the same project -> auto-chain Shot 1 from previous run's last frame!
    prev_last_frame = ctx.get_previous_run_last_frame()
    if prev_last_frame:
        print(f"🔗 Found previous run's final frame: {prev_last_frame}")
        print("   -> Shot 1 of this run will automatically use it as its starting continuity anchor!")

    for i, shot in enumerate(manifest):
        idx = shot.get("shot_index", i + 1)
        tag = ctx.shot_tag(idx)

        # If this is Shot 1 and we have prev_last_frame, copy it directly as Shot 1's base panel (or img2img)
        if i == 0 and prev_last_frame:
            dest_shot1 = os.path.join(ctx.panels_dir, f"{tag}_00001_.png")
            shutil.copy(prev_last_frame, dest_shot1)
            print(f"✅ [Shot 1 Continuity] Inherited last frame of Run {ctx.run_id - 1} -> {os.path.basename(dest_shot1)}")
            continue

        with open(WF1_PATH, "r") as f:
            wf1 = json.load(f)

        wf1["20"]["inputs"]["value"] = ctx.style_prefix
        wf1["21"]["inputs"]["value"] = ctx.char_anchor
        lighting_str = shot.get("lighting", "bright cinematic lighting, well-lit scene")
        wf1["22"]["inputs"]["value"] = f"{shot.get('shot_type', '')}, {shot.get('action', '')}, {lighting_str}, bright well-lit comic art, clear visibility".strip(", ")

        wf1["3"]["inputs"]["latent_image"] = ["5", 0]
        wf1["3"]["inputs"]["denoise"] = 1.0
        wf1["3"]["inputs"]["seed"] = shot.get("seed", 100000 + idx)
        wf1["9"]["inputs"]["filename_prefix"] = f"projects/{ctx.project_name}/{ctx.run_name}/panels/{tag}"

        wf1 = sanitize_wf1(wf1, is_img2img=False)
        res = queue_prompt(wf1)
        if res:
            print(f"✅ Queued Panel [{tag}] -> Prompt ID: {res.get('prompt_id')}")
        time.sleep(0.2)

    if wait_for_completion:
        wait_for_queue_to_finish()

# ==============================================================================
# STAGE 2: HIGH-SPEED SEQUENTIAL VIDEO GENERATION (WAN 2.1 I2V)
# ==============================================================================
def build_fast_wf2(ctx, start_image_filename, motion_prompt, seed, output_prefix):
    with open(WF2_PATH, "r") as f:
        wf2 = json.load(f)

    # Replace Node 10 with standard LoadImage so we never depend on fragile directory indexing
    wf2["10"] = {
        "inputs": {
            "image": start_image_filename,
            "upload": "image"
        },
        "class_type": "LoadImage",
        "_meta": {"title": "Load Start Frame PNG"}
    }

    # Resolution tuning
    if ctx.wan_resolution == "480p":
        wf2["14"]["inputs"]["width"] = 832
        wf2["14"]["inputs"]["height"] = 480
    else:
        wf2["14"]["inputs"]["width"] = 1280
        wf2["14"]["inputs"]["height"] = 720

    wf2["12"]["inputs"]["text"] = f"{motion_prompt}, {ctx.char_anchor}"
    wf2["15"]["inputs"]["seed"] = seed
    wf2["17"]["inputs"]["filename_prefix"] = output_prefix

    # Check if 4-step LightX2V Speed LoRA is available
    loras = get_available_loras()
    speed_lora = "wan2.1_i2v_720p_lightx2v_4step_lora.safetensors"
    if ctx.use_speed_lora and speed_lora in loras:
        wf2["99"] = {
            "inputs": {
                "lora_name": speed_lora,
                "strength_model": 1.0,
                "model": ["1", 0]
            },
            "class_type": "LoraLoaderModelOnly",
            "_meta": {"title": "Wan 2.1 4-Step Speed LoRA"}
        }
        wf2["15"]["inputs"]["model"] = ["99", 0]
        wf2["15"]["inputs"]["steps"] = 6
        wf2["15"]["inputs"]["cfg"] = 1.0
    else:
        wf2["15"]["inputs"]["steps"] = ctx.wan_steps
        wf2["15"]["inputs"]["cfg"] = 5.0

    return wf2

def stage2_generate_videos(ctx, wait_for_completion=True):
    manifest = get_manifest(ctx)
    if not manifest:
        print("❌ No manifest found!")
        return

    os.makedirs(COMFYUI_INPUT_DIR, exist_ok=True)
    os.makedirs(ctx.videos_dir, exist_ok=True)

    loras = get_available_loras()
    using_lora = ctx.use_speed_lora and ("wan2.1_i2v_720p_lightx2v_4step_lora.safetensors" in loras)
    eff_steps = 6 if using_lora else ctx.wan_steps
    print(f"\n🎥 [STAGE 2] Generating {len(manifest)} Videos | Res: {ctx.wan_resolution} | Steps: {eff_steps} (Speed LoRA: {using_lora})")

    prev_clip_last_frame = ctx.get_previous_run_last_frame()

    for i, shot in enumerate(manifest):
        idx = shot.get("shot_index", i + 1)
        tag = ctx.shot_tag(idx)
        motion_prompt = shot.get("motion_prompt", "Maintain 2D comic style. Smooth camera motion.")
        seed = shot.get("seed", 90000 + idx)

        # Determine start frame for this shot:
        # 1. If Shot 1 and previous run exists for this project -> use previous run's last frame
        # 2. If ctx.chain_within_run is True and i > 0 -> use last frame of shot i
        # 3. Otherwise -> use Stage 1 panel for shot idx
        start_png_path = None
        if i == 0 and prev_clip_last_frame and os.path.exists(prev_clip_last_frame):
            start_png_path = prev_clip_last_frame
            print(f"🔗 [{tag}] Using final frame from Run {ctx.run_id - 1} for seamless run continuation!")
        elif ctx.chain_within_run and i > 0 and prev_clip_last_frame and os.path.exists(prev_clip_last_frame):
            start_png_path = prev_clip_last_frame
            print(f"🔗 [{tag}] Chaining final frame from previous shot!")
        else:
            matches = sorted(glob.glob(os.path.join(ctx.panels_dir, f"{tag}*.png")))
            if matches:
                start_png_path = matches[-1]

        if not start_png_path or not os.path.exists(start_png_path):
            print(f"⚠️ Missing start panel for {tag} in {ctx.panels_dir}. Skipping...")
            continue

        # Copy start image into ComfyUI/input so LoadImage can read it directly
        input_filename = f"start_{tag}.png"
        shutil.copy(start_png_path, os.path.join(COMFYUI_INPUT_DIR, input_filename))

        output_prefix = f"projects/{ctx.project_name}/{ctx.run_name}/videos/{tag}"
        wf2 = build_fast_wf2(ctx, input_filename, motion_prompt, seed, output_prefix)

        res = queue_prompt(wf2)
        if res:
            print(f"🎞️ Queued Video [{tag}.mp4] -> Prompt ID: {res.get('prompt_id')}")

        # If chaining shot-to-shot within a run, wait for each clip so we can extract its last frame
        if ctx.chain_within_run:
            wait_for_queue_to_finish()
            rendered_mp4s = sorted(glob.glob(os.path.join(ctx.videos_dir, f"{tag}*.mp4")))
            if rendered_mp4s:
                chained_png = os.path.join(ctx.panels_dir, f"{tag}_lastframe.png")
                if extract_last_frame(rendered_mp4s[-1], chained_png):
                    prev_clip_last_frame = chained_png

    if wait_for_completion and not ctx.chain_within_run:
        wait_for_queue_to_finish()

    # Save the last frame of the final video of this run for Run N+1!
    all_mp4s = sorted(glob.glob(os.path.join(ctx.videos_dir, f"{ctx.project_name}_run{ctx.run_id}_shot*.mp4")))
    if all_mp4s:
        run_last_frame = os.path.join(ctx.final_dir, f"{ctx.project_name}_run{ctx.run_id}_last_frame.png")
        if extract_last_frame(all_mp4s[-1], run_last_frame):
            ctx.save_project_meta(last_frame_path=run_last_frame)
            print(f"💾 Saved final frame of Run {ctx.run_id} for future runs: {run_last_frame}")

# ==============================================================================
# STAGE 3: AUDIO GENERATION + MUXING + AUTOMATIC RUN STITCHING
# ==============================================================================
def stage3_audio_and_stitch(ctx):
    manifest = get_manifest(ctx)
    if not manifest:
        print("❌ No manifest found!")
        return

    print(f"\n🔊 [STAGE 3] Generating Voiceover + SFX & Stitching Master Video for {ctx.project_name} (Run {ctx.run_id})...")
    os.makedirs(ctx.audio_dir, exist_ok=True)
    os.makedirs(ctx.final_dir, exist_ok=True)

    muxed_clips = []
    for i, shot in enumerate(manifest):
        idx = shot.get("shot_index", i + 1)
        tag = ctx.shot_tag(idx)

        raw_mp4s = sorted(glob.glob(os.path.join(ctx.videos_dir, f"{tag}*.mp4")))
        if not raw_mp4s:
            print(f"⚠️ No rendered video found for {tag}, skipping...")
            continue
        raw_mp4 = raw_mp4s[-1]

        # Generate voiceover + ambient SFX
        audio_aac = generate_shot_audio(ctx, shot)
        muxed_mp4 = os.path.join(ctx.final_dir, f"{tag}_muxed.mp4")

        if audio_aac and os.path.exists(audio_aac):
            cmd = [
                "ffmpeg", "-y",
                "-i", raw_mp4,
                "-i", audio_aac,
                "-c:v", "copy", "-c:a", "aac", "-shortest", muxed_mp4
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            shutil.copy(raw_mp4, muxed_mp4)

        if os.path.exists(muxed_mp4):
            muxed_clips.append(muxed_mp4)
            print(f"   ✔ Muxed audio + video: {os.path.basename(muxed_mp4)}")

    if not muxed_clips:
        print("❌ No clips available to stitch!")
        return

    # Write ffmpeg concat list
    concat_list_path = os.path.join(ctx.final_dir, "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for clip_p in muxed_clips:
            f.write(f"file '{clip_p}'\n")

    master_run_mp4 = os.path.join(ctx.final_dir, f"{ctx.project_name}_run{ctx.run_id}_master.mp4")
    concat_cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list_path, "-c", "copy", master_run_mp4
    ]
    subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\n🎬 Master Run Video Stitched: {master_run_mp4}")

    # Extract final frame of the stitched master video for Run N+1 continuity
    run_last_frame = os.path.join(ctx.final_dir, f"{ctx.project_name}_run{ctx.run_id}_last_frame.png")
    if extract_last_frame(master_run_mp4, run_last_frame):
        ctx.save_project_meta(last_frame_path=run_last_frame)
        print(f"🔗 Extracted final frame for Run {ctx.run_id + 1} continuation: {run_last_frame}")

    # If multiple runs exist in this project, also stitch a Project Complete Movie!
    all_run_masters = sorted(glob.glob(os.path.join(ctx.project_dir, "run*", "final", f"{ctx.project_name}_run*_master.mp4")))
    if len(all_run_masters) > 1:
        proj_concat = os.path.join(ctx.project_dir, "project_concat.txt")
        with open(proj_concat, "w") as f:
            for rm in all_run_masters:
                f.write(f"file '{rm}'\n")
        proj_master = os.path.join(ctx.project_dir, f"{ctx.project_name}_COMPLETE_SERIES.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", proj_concat, "-c", "copy", proj_master],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"🌟 Multi-Run Project Complete Movie Updated: {proj_master}")

# ==============================================================================
# INTERACTIVE CLI MENU
# ==============================================================================
def main():
    ctx = StudioContext(project_name="Arjun", run_id=1)

    while True:
        print("\n==================================================================")
        print("🎬 COMIC-TO-VIDEO CLI PRODUCTION STUDIO (v5 - Fast GPU Edition)")
        print("==================================================================")
        print(f"📂 Active Project : {ctx.project_name}  (Run: run{ctx.run_id})")
        print(f"🏷️  Shot Naming    : {ctx.shot_tag(1)} ... {ctx.shot_tag(10)}")
        print(f"⚡ Video Settings : {ctx.wan_resolution} | Steps: {ctx.wan_steps} | 4-Step Speed LoRA: {ctx.use_speed_lora}")
        print(f"🔗 Intra-Run Chain: {'ON (Shot N->N+1)' if ctx.chain_within_run else 'OFF (Run N->Run N+1 Auto-Active)'}")
        print("------------------------------------------------------------------")
        print("1. Run Full Automated Pipeline (Stage 1 Panels -> Stage 2 Videos -> Stage 3 Audio & Stitch)")
        print("2. Stage 1 Only: Generate Clean Panels")
        print("3. Stage 2 Only: Generate Sequential Videos (Fast Wan 2.1 I2V)")
        print("4. Stage 3 Only: Generate Voiceover/SFX & Stitch Master Video")
        print("5. Switch Project Name / Advance to Next Run (e.g., Arjun -> run2)")
        print("6. Speed & Continuity Settings (Toggle 720p/480p, Steps, Shot Chaining)")
        print("7. Exit")
        print("==================================================================")

        choice = input("Select an option (1-7): ").strip()
        if choice == "1":
            stage1_generate_panels(ctx, wait_for_completion=True)
            stage2_generate_videos(ctx, wait_for_completion=True)
            stage3_audio_and_stitch(ctx)
        elif choice == "2":
            stage1_generate_panels(ctx, wait_for_completion=True)
        elif choice == "3":
            stage2_generate_videos(ctx, wait_for_completion=True)
        elif choice == "4":
            stage3_audio_and_stitch(ctx)
        elif choice == "5":
            new_p = input(f"Enter Project Name (current: {ctx.project_name}): ").strip() or ctx.project_name
            new_r = input(f"Enter Run Number (current: {ctx.run_id}): ").strip() or str(ctx.run_id)
            ctx = StudioContext(project_name=new_p, run_id=int(new_r))
            print(f"✅ Switched to Project '{ctx.project_name}', Run {ctx.run_id}")
        elif choice == "6":
            res_in = input("Resolution? (1 = 720p [1280x720], 2 = 480p [832x480 - 3x faster]): ").strip()
            if res_in == "2":
                ctx.wan_resolution = "480p"
            elif res_in == "1":
                ctx.wan_resolution = "720p"
            steps_in = input(f"Sampling steps without Speed LoRA (current {ctx.wan_steps}, recommended 12-16): ").strip()
            if steps_in.isdigit():
                ctx.wan_steps = int(steps_in)
            lora_in = input("Use 4-Step LightX2V Speed LoRA if downloaded? (Y/n): ").strip().lower()
            ctx.use_speed_lora = (lora_in != "n")
            chain_in = input("Chain last frame of every shot inside the same run too? (y/N): ").strip().lower()
            ctx.chain_within_run = (chain_in == "y")
        elif choice == "7":
            print("Exiting CLI Studio. Goodbye!")
            break
        else:
            print("❌ Invalid option.")

if __name__ == "__main__":
    main()
