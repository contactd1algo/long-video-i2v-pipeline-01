#!/usr/bin/env python3
"""
Comic-to-Video CLI Production Studio  (v6 - "bibles" edition)

Patched version of run_cli_studio.py from contactd1algo/long-video-i2v-pipeline-01.
Everything in the original still works (Stage 1 panels -> Stage 2 Wan I2V -> Stage 3 audio + stitch).
Added:
  * <project>/bibles.json  - character / setting / voice / music "bible". Descriptors are injected word-for-word
                             into every shot's Flux prompt (and a short form into every Wan prompt) based on the
                             shot's  "characters": [...]  and  "setting": "KEY"  fields.
  * speaker -> voice map (edge-tts, with optional per-character rate/pitch), TTS respelling of invented names.
  * per-scene background music (music/<CUE>.mp3|wav, optional generator command, procedural fallback) that
    continues across shots of the same cue and ducks under dialogue.
  * prompt-keyword procedural Foley beds (wind, water, fire, chimes, crowd ... ) instead of the sci-fi drones.
  * text overlays (title / acknowledgement / recap) burned in with ffmpeg drawtext.
  * "continue_from_previous": false  -> shot 1 of a run starts from its own panel (title clips of episodes 2+).
  * "reuse_clip": {"run":N,"shot":M}  -> recap clips re-use an earlier rendered clip (no GPU, perfect consistency).
  * "placeholder": true               -> shot is skipped everywhere (credits slot to be filled in later).
  * "external_clip": "path.mp4"       -> use a ready-made video for that shot.
  * episode stacking (episode_map.json) and natural run ordering (run2 before run10).
  * resume-friendly: existing panels / videos are skipped unless --force; --shots 3,5 re-does only those shots.
  * --dry-run / prompt preview: export every exact prompt without touching the GPU.
Non-interactive:  python run_cli_studio.py --project LanternSister --runs 1-10 --stage all
"""
import os, sys, re, json, time, glob, math, wave, struct, shutil, subprocess, argparse
import urllib.request, urllib.error

COMFYUI_HOST = os.environ.get("COMFYUI_HOST", "http://127.0.0.1:8188")
DEFAULT_PROJECT = os.environ.get("STUDIO_PROJECT", "LanternSister")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DRY_RUN = False
DRY_LOG = []
AUDIO_EXTS = ("mp3", "wav", "m4a", "ogg", "flac")
SR = 24000


def detect_comfy_dir():
    env = os.environ.get("COMFYUI_DIR")
    if env:
        return env
    for candidate in ["/workspace/runpod-slim/ComfyUI", "/workspace/ComfyUI", "/ComfyUI", "/opt/ComfyUI"]:
        if os.path.isdir(candidate):
            return candidate
    return "/workspace/runpod-slim/ComfyUI"


COMFYUI_DIR = detect_comfy_dir()
COMFYUI_OUTPUT_DIR = os.path.join(COMFYUI_DIR, "output")
COMFYUI_INPUT_DIR = os.path.join(COMFYUI_DIR, "input")


def _wf_path(name):
    for base in (os.getcwd(), SCRIPT_DIR):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return name


WF1_PATH = _wf_path("workflow1_panel_generator_and_tweaker.json")
WF2_PATH = _wf_path("workflow2_sequential_i2v_wan21.json")

DEFAULT_STYLE_PREFIX = (
    "clean 2D graphic novel illustration, modern comic art style, crisp ink lineart, "
    "flat vibrant cel-shading, bright studio lighting, well-lit scene, vivid colors, "
    "high visibility, 16:9 cinematic framing, no borders, uncropped"
)
DEFAULT_CHAR_ANCHOR = "consistent character designs across every shot"


def nkey(s):
    """natural sort key: run2 < run10, shot2 < shot10"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def find_files(directory, tag, ext):
    """files named <tag>_*.<ext> (so shot1 never matches shot10), natural-sorted"""
    return sorted(glob.glob(os.path.join(directory, f"{tag}_*.{ext}")), key=nkey)


def parse_ranges(spec):
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


# ==============================================================================
# PROJECT -> RUN -> SHOT HIERARCHY MANAGER
# ==============================================================================
class StudioContext:
    def __init__(self, project_name=DEFAULT_PROJECT, run_id=1):
        self.project_name = project_name
        self.run_id = int(run_id)
        self.style_prefix = DEFAULT_STYLE_PREFIX
        self.char_anchor = DEFAULT_CHAR_ANCHOR
        self.bibles = {}
        self.wan_steps = 15
        self.seed_offset = 0          # --reseed N: added to every Flux/Wan seed so a re-roll gives a different image
        self.lora_steps = int(os.environ.get("WAN_LORA_STEPS", "6"))   # steps when the speed LoRA is active (4 is the LoRA's native value)
        self.wan_resolution = "720p"
        self.use_speed_lora = True
        self.chain_within_run = False
        self.force = False                 # re-render even if output exists
        self.only_shots = None             # set of shot_index values, None = all
        self.procedural_music = True       # fall back to synthesized music when no track is supplied
        self.sfx_volume = 0.30
        self.music_volume = 0.20
        self.vo_volume = 1.5
        self.load_or_init_project()

    # ---- paths
    @property
    def project_dir(self): return os.path.join(COMFYUI_OUTPUT_DIR, "projects", self.project_name)
    @property
    def project_meta_path(self): return os.path.join(self.project_dir, "project_config.json")
    @property
    def bibles_path(self): return os.path.join(self.project_dir, "bibles.json")
    @property
    def music_dir(self): return os.path.join(self.project_dir, "music")
    @property
    def episodes_dir(self): return os.path.join(self.project_dir, "episodes")
    @property
    def run_name(self): return f"run{self.run_id}"
    @property
    def run_dir(self): return os.path.join(self.project_dir, self.run_name)
    @property
    def panels_dir(self): return os.path.join(self.run_dir, "panels")
    @property
    def videos_dir(self): return os.path.join(self.run_dir, "videos")
    @property
    def audio_dir(self): return os.path.join(self.run_dir, "audio")
    @property
    def final_dir(self): return os.path.join(self.run_dir, "final")
    @property
    def chain_dir(self): return os.path.join(self.run_dir, "chain")

    def shot_tag(self, shot_idx):
        return f"{self.project_name}_run{self.run_id}_shot{shot_idx}"

    def shot_selected(self, idx):
        return self.only_shots is None or int(idx) in self.only_shots

    def load_or_init_project(self):
        for d in (self.panels_dir, self.videos_dir, self.audio_dir, self.final_dir):
            os.makedirs(d, exist_ok=True)
        if os.path.exists(self.project_meta_path):
            try:
                with open(self.project_meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self.style_prefix = meta.get("style_prefix", DEFAULT_STYLE_PREFIX)
                self.char_anchor = meta.get("char_anchor", DEFAULT_CHAR_ANCHOR)
            except Exception:
                pass
        else:
            self.save_project_meta()
        if os.path.exists(self.bibles_path):
            try:
                with open(self.bibles_path, "r", encoding="utf-8") as f:
                    self.bibles = json.load(f)
            except Exception as e:
                print(f"⚠️ Could not read bibles.json: {e}")

    def save_project_meta(self, last_frame_path=None):
        # NOTE: bibles live in bibles.json (never rewritten here), so they cannot be wiped by this save.
        meta = {
            "project_name": self.project_name,
            "style_prefix": self.style_prefix,
            "char_anchor": self.char_anchor,
            "latest_run_id": self.run_id,
            "last_frame_path": last_frame_path,
        }
        os.makedirs(self.project_dir, exist_ok=True)
        with open(self.project_meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    def get_previous_run_last_frame(self):
        if self.run_id <= 1:
            return None
        prev_run_dir = os.path.join(self.project_dir, f"run{self.run_id - 1}")
        prev_last_frame = os.path.join(prev_run_dir, "final", f"{self.project_name}_run{self.run_id - 1}_last_frame.png")
        if os.path.exists(prev_last_frame):
            return prev_last_frame
        prev_videos = sorted(glob.glob(os.path.join(prev_run_dir, "videos", "*.mp4")), key=nkey)
        if prev_videos:
            os.makedirs(os.path.join(prev_run_dir, "final"), exist_ok=True)
            if extract_last_frame(prev_videos[-1], prev_last_frame):
                return prev_last_frame
        return None


# ==============================================================================
# BIBLES -> PROMPT COMPOSITION
# ==============================================================================
def _chars(ctx):
    return ctx.bibles.get("characters", {})


def ordered_chars(ctx, keys):
    """characters of a shot in canonical bible order (stable text across shots)"""
    order = list(_chars(ctx).keys())
    known = [k for k in keys if k in _chars(ctx)]
    return sorted(known, key=lambda k: order.index(k))


def compose_flux_layers(ctx, shot):
    """returns (style, anchor, scene) strings for Workflow 1 nodes 20 / 21 / 22"""
    world = ctx.bibles.get("world_anchor") or ctx.char_anchor
    keys = ordered_chars(ctx, shot.get("characters", []))
    cast = ""
    if keys:
        form = "mid" if len(keys) >= 4 else "look"       # compact descriptors for crowded frames
        cast = "Characters: " + "; ".join(
            f"{_chars(ctx)[k]['tag']} ({_chars(ctx)[k].get(form) or _chars(ctx)[k]['look']})" for k in keys)
    setting = ctx.bibles.get("settings", {}).get(shot.get("setting", ""), "")
    setting = f"Setting: {setting}" if setting else ""
    anchor = ". ".join(x for x in (world, cast, setting) if x)
    lighting = shot.get("lighting") or "bright cinematic lighting, well-lit scene"
    scene = ", ".join(x for x in (shot.get("shot_type", ""), shot.get("action", ""), lighting,
                                  "bright well-lit comic art, clear visibility") if x)
    return ctx.style_prefix, anchor, scene


def compose_wan_prompt(ctx, shot):
    base = (shot.get("motion_prompt") or "Maintain 2D cel-shaded graphic novel style. Smooth camera motion.").strip()
    base = base.rstrip(".,; ") + "."
    keys = ordered_chars(ctx, shot.get("characters", []))
    if keys:
        cast = "; ".join(f"{_chars(ctx)[k]['tag']} ({_chars(ctx)[k]['short']})" for k in keys)
        return f"{base} {cast}."
    return f"{base} {ctx.char_anchor}"


def find_leaks(ctx, text):
    return [w for w in ctx.bibles.get("forbidden_in_images", []) if re.search(r"\b" + re.escape(w) + r"\b", text)]


# ---- shot kinds ------------------------------------------------------------
def is_placeholder(shot): return bool(shot.get("placeholder"))
def reuse_spec(shot): return shot.get("reuse_clip")
def external_clip(shot): return shot.get("external_clip")


def needs_generation(shot):
    return not (is_placeholder(shot) or reuse_spec(shot) or external_clip(shot))


def skip_reason(shot):
    if is_placeholder(shot): return "placeholder (credits slot) - skipped"
    if reuse_spec(shot): return f"re-uses clip run{shot['reuse_clip']['run']}/shot{shot['reuse_clip']['shot']} - no render needed"
    if external_clip(shot): return "uses external clip - no render needed"
    return ""


def starts_fresh(manifest):
    return bool(manifest) and manifest[0].get("continue_from_previous") is False


# ==============================================================================
# COMFYUI API & QUEUE MONITOR
# ==============================================================================
def queue_prompt(workflow_dict):
    if DRY_RUN:
        DRY_LOG.append(workflow_dict)
        return {"prompt_id": f"dry-run-{len(DRY_LOG)}"}
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
    if DRY_RUN:
        return
    print("⏳ Waiting for ComfyUI queue to complete rendering...")
    start_t = time.time()
    while True:
        try:
            with urllib.request.urlopen(f"{COMFYUI_HOST}/queue") as resp:
                q_data = json.loads(resp.read().decode("utf-8"))
            running = len(q_data.get("queue_running", []))
            pending = len(q_data.get("queue_pending", []))
            elapsed = int(time.time() - start_t)
            sys.stdout.write(f"\r 🔄 Active: {running} | Pending: {pending} | Elapsed: {elapsed}s ")
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
    return [f for f in os.listdir(lora_dir)
            if f.endswith(".safetensors") and os.path.getsize(os.path.join(lora_dir, f)) > 1_000_000]


# ==============================================================================
# FFMPEG HELPERS
# ==============================================================================
def run_ff(cmd):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def probe_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def probe_size(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height", "-of", "csv=p=0:s=x", path],
                             capture_output=True, text=True).stdout.strip()
        w, h = out.split("x")
        return int(w), int(h)
    except Exception:
        return 1280, 720


def has_audio_stream(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=codec_type", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return "audio" in out


def extract_last_frame(video_path, output_png_path):
    if not os.path.exists(video_path):
        return False
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
    cmd = ["ffmpeg", "-y", "-sseof", "-0.1", "-i", video_path, "-update", "1", "-q:v", "1", output_png_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.exists(output_png_path)


def concat_videos(files, out_path):
    """concat + one clean re-encode (robust against slightly different encoder headers)"""
    lst = out_path + ".concat.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in files:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
           "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", out_path]
    r = run_ff(cmd)
    try: os.remove(lst)
    except OSError: pass
    return r.returncode == 0 and os.path.exists(out_path)


# ==============================================================================
# AUDIO ENGINE 1: PROCEDURAL FOLEY BEDS (numpy, deterministic per shot seed)
# ==============================================================================
def _np():
    import numpy as np
    return np


def _shaped_noise(np, rng, n, lo, hi):
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mask = (1.0 / (1.0 + (lo / np.maximum(f, 1e-3)) ** 4)) * (1.0 / (1.0 + (f / hi) ** 4))
    y = np.fft.irfft(X * mask, n)
    return y / (np.max(np.abs(y)) + 1e-9)


def _lfo(np, rng, n, rate_hz):
    k = max(3, int(n / SR * rate_hz * 3) + 2)
    return np.interp(np.linspace(0, k - 1, n), np.arange(k), rng.random(k))


def _burst(np, n, start, sig, env_len):
    out = np.zeros(n)
    s = int(start * SR)
    if s >= n:
        return out
    m = min(len(sig), n - s)
    out[s:s + m] = sig[:m]
    return out


def sfx_layer(np, name, n, rng):
    t = np.arange(n) / SR
    dur = n / SR
    if name == "wind":
        return _shaped_noise(np, rng, n, 150, 1100) * (0.30 + 0.70 * _lfo(np, rng, n, 0.35))
    if name == "water":
        base = _shaped_noise(np, rng, n, 450, 3800) * (0.45 + 0.55 * _lfo(np, rng, n, 9))
        for st in rng.random(max(2, int(dur * 1.2))) * dur:
            f0 = 350 + rng.random() * 500
            tt = np.arange(int(0.09 * SR)) / SR
            plop = np.sin(2 * np.pi * (f0 + 900 * tt) * tt) * np.exp(-tt * 40)
            base = base + 0.8 * _burst(np, n, st, plop, 0)
        return base
    if name == "fire":
        low = _shaped_noise(np, rng, n, 40, 450) * 0.5
        pops = np.zeros(n)
        idx = np.where(rng.random(n) < 22.0 / SR)[0]
        pops[idx] = rng.random(len(idx)) * 2 - 1
        kern = np.exp(-np.arange(int(0.012 * SR)) / (0.003 * SR)) * np.sign(rng.standard_normal(int(0.012 * SR)))
        crack = np.convolve(pops, kern, mode="same")
        return low + 1.2 * crack / (np.max(np.abs(crack)) + 1e-9)
    if name == "chimes":
        out = np.zeros(n)
        notes = [523.3, 587.3, 659.3, 784.0, 880.0, 1046.5]
        st = 0.1
        while st < dur - 0.4:
            f = notes[int(rng.integers(0, len(notes)))]
            tt = np.arange(int(1.6 * SR)) / SR
            bell = (np.sin(2 * np.pi * f * tt) + 0.4 * np.sin(2 * np.pi * f * 2.76 * tt) + 0.2 * np.sin(2 * np.pi * f * 5.4 * tt)) * np.exp(-tt * 3.2)
            out += 0.7 * _burst(np, n, st, bell, 0)
            st += 0.7 + rng.random() * 1.1
        return out
    if name == "magic_hum":
        trem = 0.75 + 0.25 * np.sin(2 * np.pi * 0.3 * t)
        pad = (np.sin(2 * np.pi * 164.8 * t) + 0.6 * np.sin(2 * np.pi * 247.2 * t + 1.0) +
               0.4 * np.sin(2 * np.pi * 330.2 * t + 2.0) + 0.25 * np.sin(2 * np.pi * 494.5 * t)) * trem
        shimmer = np.sin(2 * np.pi * 1320 * t) * (0.04 + 0.05 * _lfo(np, rng, n, 0.6)) * (_lfo(np, rng, n, 2) > 0.5)
        return pad + shimmer
    if name == "rumble":
        r = _shaped_noise(np, rng, n, 18, 90) * (0.5 + 0.5 * _lfo(np, rng, n, 0.6)) * 1.4
        for st in rng.random(2) * dur:
            tt = np.arange(int(0.5 * SR)) / SR
            r = r + 0.6 * _burst(np, n, st, np.sin(2 * np.pi * 48 * tt) * np.exp(-tt * 7), 0)
        return r
    if name in ("creak", "door_creak"):
        freq = 95 + 28 * np.sin(2 * np.pi * 0.6 * t) + 12 * np.sin(2 * np.pi * 3.1 * t)
        ph = 2 * np.pi * np.cumsum(freq) / SR
        return (np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.25 * _shaped_noise(np, rng, n, 300, 1800)) * (0.4 + 0.6 * _lfo(np, rng, n, 0.8))
    if name == "birds":
        out = np.zeros(n)
        st = 0.2
        while st < dur - 0.3:
            for k in range(int(rng.integers(2, 4))):
                tt = np.arange(int(0.13 * SR)) / SR
                f0 = 2400 + rng.random() * 800
                chirp = np.sin(2 * np.pi * (f0 + 1500 * tt / 0.13) * tt) * np.sin(np.pi * tt / 0.13) ** 2
                out += 0.5 * _burst(np, n, st + k * 0.17, chirp, 0)
            st += 1.0 + rng.random() * 1.6
        return out
    if name == "crowd":
        return _shaped_noise(np, rng, n, 250, 2300) * (0.35 + 0.65 * _lfo(np, rng, n, 0.8)) * (0.7 + 0.3 * np.sin(2 * np.pi * 4.2 * t))
    if name == "footsteps":
        out = np.zeros(n)
        st = 0.25
        while st < dur - 0.2:
            tt = np.arange(int(0.16 * SR)) / SR
            step = np.sin(2 * np.pi * 85 * tt) * np.exp(-tt * 28) + 0.35 * rng.standard_normal(len(tt)) * np.exp(-tt * 60)
            out += _burst(np, n, st, step, 0)
            st += 0.52 + rng.random() * 0.1
        return out
    # legacy sci-fi types from the original pipeline (still supported)
    if name == "server_hum":
        return 0.7 * np.sin(2 * np.pi * 60 * t) + 0.35 * np.sin(2 * np.pi * 120 * t)
    if name == "alarm":
        return (np.where((t * 2.5).astype(int) % 2 == 0, 1.0, 0.25) * np.sin(2 * np.pi * 660 * t) + 0.5 * np.sin(2 * np.pi * 110 * t))
    if name == "power_down":
        return np.sin(2 * np.pi * np.maximum(45.0, 320.0 * np.exp(-0.55 * t)) * t)
    if name == "beeps":
        return np.where((t % 0.6 > 0.1) & (t % 0.6 < 0.22), 1.0, 0.0) * np.sin(2 * np.pi * 1200 * t)
    # room_tone / unknown
    return 0.25 * _shaped_noise(np, rng, n, 60, 500)


def synth_sfx_wav(sfx_type, output_wav, duration=5.0, seed=1):
    np = _np()
    n = int(duration * SR)
    rng = np.random.default_rng(int(seed))
    names = [x.strip() for x in str(sfx_type or "room_tone").replace(",", "+").split("+") if x.strip()] or ["room_tone"]
    mix = np.zeros(n)
    for nm in names[:3]:
        layer = sfx_layer(np, nm, n, rng)
        mix += layer / (np.max(np.abs(layer)) + 1e-9)
    mix = mix / (np.max(np.abs(mix)) + 1e-9) * 0.6
    fade = int(0.04 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    write_wav(output_wav, mix)


def write_wav(path, arr):
    np = _np()
    pcm = (np.clip(arr, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ==============================================================================
# AUDIO ENGINE 2: BACKGROUND MUSIC (user track > generator command > project bgm > procedural)
# ==============================================================================
def synth_music(prompt, out_wav, seconds=96, seed=11):
    """small procedural score (pad + plucked pentatonic line) driven by the cue text and its bpm"""
    np = _np()
    p = (prompt or "").lower()
    m = re.search(r"(\d+)\s*bpm", p)
    bpm = int(m.group(1)) if m else 80
    eerie = any(k in p for k in ("eerie", "uneasy", "stealth", "cold", "tense"))
    minor = eerie or any(k in p for k in ("tender", "emotional", "reflective", "solemn", "urgent", "nostalgic", "serene"))
    playful = any(k in p for k in ("playful", "festive", "folk", "fiddle", "caravan", "kalimba", "pizzicato", "adventurous", "triumphant"))
    slow = any(k in p for k in ("tender", "serene", "ambient", "drone", "reflective", "wind"))
    sparkle = any(k in p for k in ("chime", "glock", "harp", "kalimba", "bell"))
    root = 57 if minor else 60
    scale = [0, 3, 5, 7, 10] if minor else [0, 2, 4, 7, 9]
    prog = [0, -4, -2, -5] if minor else [0, -3, -7, -5]
    third = 3 if minor else 4
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    mtof = lambda mid: 440.0 * 2 ** ((mid - 69) / 12.0)
    beat = 60.0 / bpm
    bar = beat * 4
    out = np.zeros(n)
    nb = int(seconds / bar) + 1
    for b in range(nb):
        r = root + prog[b % 4] - 12
        tones = [r, r + 6, r + 12] if eerie else [r, r + 7, r + 12, r + 12 + third]
        s0, s1 = int(b * bar * SR), min(n, int((b + 1) * bar * SR + 0.8 * SR))
        if s0 >= n:
            break
        tt = t[s0:s1] - b * bar
        env = np.clip(tt / (0.45 * bar), 0, 1) * np.clip(((bar + 0.8) - tt) / 0.8, 0, 1)
        for mid in tones:
            f = mtof(mid)
            out[s0:s1] += 0.085 * env * (np.sin(2 * np.pi * f * tt) + np.sin(2 * np.pi * (f * 1.003) * tt))
    step = beat * (2 if slow else (0.5 if playful else 1))
    idx = 3
    st = 0.0
    while st < seconds - 1.2:
        idx = int(np.clip(idx + rng.integers(-2, 3), 0, 9))
        mid = root + scale[idx % 5] + 12 * (idx // 5)
        f = mtof(mid)
        L = int(1.3 * SR)
        s0 = int(st * SR)
        tt = np.arange(min(L, n - s0)) / SR
        pluck = (np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(4 * np.pi * f * tt) + 0.15 * np.sin(6 * np.pi * f * tt)) * np.exp(-tt * 3.6)
        acc = 0.17 if rng.random() < 0.3 else 0.11
        out[s0:s0 + len(tt)] += acc * pluck
        if sparkle and rng.random() < 0.35:
            f2 = mtof(mid + 12)
            sp = np.sin(2 * np.pi * f2 * tt) * np.exp(-tt * 6.0)
            out[s0:s0 + len(tt)] += 0.07 * sp
        st += step
    out = out / (np.max(np.abs(out)) + 1e-9) * 0.7
    fade = int(0.05 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    write_wav(out_wav, out)


_bgm_dur_cache = {}


def resolve_bgm_file(ctx, cue, prompt):
    if cue:
        for ext in AUDIO_EXTS:
            p = os.path.join(ctx.music_dir, f"{cue}.{ext}")
            if os.path.exists(p):
                return p
        cmd_t = os.environ.get("MUSIC_GEN_CMD")
        if cmd_t:
            gen = os.path.join(ctx.music_dir, "_gen", f"{cue}.wav")
            if not os.path.exists(gen):
                os.makedirs(os.path.dirname(gen), exist_ok=True)
                cmd = cmd_t.format(prompt=prompt.replace('"', "'"), out=gen, seconds=90)
                print(f"🎼 generating music cue {cue} ...")
                subprocess.run(cmd, shell=True)
            if os.path.exists(gen):
                return gen
    for ext in AUDIO_EXTS:
        p = os.path.join(ctx.project_dir, f"bgm.{ext}")
        if os.path.exists(p):
            return p
    if cue and ctx.procedural_music and prompt:
        auto = os.path.join(ctx.music_dir, "_auto", f"{cue}.wav")
        if not os.path.exists(auto):
            os.makedirs(os.path.dirname(auto), exist_ok=True)
            try:
                synth_music(prompt, auto)
            except Exception as e:
                print(f"⚠️ procedural music failed for {cue}: {e}")
                return None
        return auto
    return None


# ==============================================================================
# AUDIO ENGINE 3: NEURAL TTS (speaker -> voice, respelling, tempo guard)
# ==============================================================================
def resolve_voice(ctx, shot):
    """returns (voice_id, rate, pitch)"""
    sp = shot.get("speaker", "")
    chars = ctx.bibles.get("characters", {})
    voices = ctx.bibles.get("voices", {})
    if sp and sp in chars and chars[sp].get("voice"):
        c = chars[sp]
        return c["voice"], c.get("rate"), c.get("pitch")
    if sp and sp in voices:
        v = voices[sp]
        return v["voice"], v.get("rate"), v.get("pitch")
    return shot.get("voice", "en-US-ChristopherNeural"), None, None


def tts_text(ctx, text):
    for k, v in sorted(ctx.bibles.get("tts_respell", {}).items(), key=lambda kv: -len(kv[0])):
        text = re.sub(r"\b" + re.escape(k) + r"\b", v, text)
    return text


def synthesize_vo(text, voice, out_mp3, rate=None, pitch=None):
    cmd = ["edge-tts", "--voice", voice, "--text", text, "--write-media", out_mp3]
    if rate: cmd.append(f"--rate={rate}")
    if pitch: cmd.append(f"--pitch={pitch}")
    for _ in range(3):
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            print("⚠️ edge-tts not installed (pip install edge-tts) - dialogue skipped")
            return False
        if res.returncode == 0 and os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 500:
            return True
        time.sleep(1.0)
    return False


def generate_shot_audio(ctx, shot, bgm=None):
    """5.0 s stereo AAC: Foley bed + dialogue + (ducked) music.  bgm = dict(path, offset, fade_in, fade_out)"""
    idx = shot.get("shot_index", 1)
    tag = ctx.shot_tag(idx)
    dialogue = (shot.get("dialogue") or "").strip()
    voice, rate, pitch = resolve_voice(ctx, shot)
    sfx_wav = os.path.join(ctx.audio_dir, f"{tag}_sfx.wav")
    vo_mp3 = os.path.join(ctx.audio_dir, f"{tag}_vo.mp3")
    mixed = os.path.join(ctx.audio_dir, f"{tag}_mix.m4a")

    sfx_cmd = os.environ.get("SFX_GEN_CMD")          # optional AI text-to-audio hook
    made = False
    if sfx_cmd and shot.get("sfx_prompt"):
        subprocess.run(sfx_cmd.format(prompt=shot["sfx_prompt"].replace('"', "'"), out=sfx_wav, seconds=5), shell=True)
        made = os.path.exists(sfx_wav) and os.path.getsize(sfx_wav) > 1000
    if not made:
        synth_sfx_wav(shot.get("sfx_type", "room_tone"), sfx_wav, 5.0, seed=shot.get("seed", idx))

    has_vo = False
    if dialogue:
        if os.path.exists(vo_mp3):
            os.remove(vo_mp3)
        has_vo = synthesize_vo(tts_text(ctx, dialogue), voice, vo_mp3, rate, pitch)

    cmd = ["ffmpeg", "-y", "-i", sfx_wav]
    ni = 1
    vi = bi = None
    if has_vo:
        cmd += ["-i", vo_mp3]; vi = ni; ni += 1
    if bgm:
        cmd += ["-stream_loop", "-1", "-ss", f"{bgm['offset']:.2f}", "-i", bgm["path"]]; bi = ni; ni += 1

    parts = [f"[0:a]volume={ctx.sfx_volume}[sfx]"]
    mix = ["[sfx]"]
    if has_vo:
        dur = probe_duration(vo_mp3)
        tempo = 1.0 if dur <= 4.5 else min(1.4, dur / 4.5)
        at = f"atempo={tempo:.3f}," if tempo > 1.001 else ""
        parts.append(f"[{vi}:a]{at}adelay=250|250,volume={ctx.vo_volume},asplit=2[vo][vosc]")
        mix.append("[vo]")
    if bgm:
        fx = f"volume={ctx.music_volume}"
        if bgm.get("fade_in"): fx += ",afade=t=in:st=0:d=0.6"
        if bgm.get("fade_out"): fx += ",afade=t=out:st=4.1:d=0.9"
        if has_vo:
            parts.append(f"[{bi}:a]{fx}[bgraw]")
            parts.append("[bgraw][vosc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[bgm]")
        else:
            parts.append(f"[{bi}:a]{fx}[bgm]")
        mix.append("[bgm]")
    n = len(mix)
    parts.append("".join(mix) + f"amix=inputs={n}:duration=first:dropout_transition=0,volume={n},"
                 "alimiter=limit=0.95,aformat=sample_rates=44100:channel_layouts=stereo[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-t", "5.0", mixed]
    r = run_ff(cmd)
    if r.returncode != 0 or not os.path.exists(mixed):
        print(f"⚠️ audio mix failed for {tag}: {r.stderr.decode(errors='replace')[-300:]}")
        # last-resort: silent 5 s track so every clip has an audio stream (needed for clean concat)
        run_ff(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "5.0", "-c:a", "aac", mixed])
    return mixed if os.path.exists(mixed) else None


# ==============================================================================
# TEXT OVERLAYS (title / acknowledgement / recap / credits)
# ==============================================================================
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
]
LAYOUTS = {   # (y fraction, font-height fraction) per line
    "title": [(0.40, 0.115), (0.60, 0.055)],
    "acknowledgement": [(0.77, 0.048), (0.85, 0.066)],
    "recap": [(0.07, 0.062), (0.15, 0.045)],
    "credits": [(0.40, 0.07), (0.54, 0.05)],
}


def overlay_filter(ctx, shot, tag, height, width=None):
    text = (shot.get("overlay_text") or "").strip()
    if not text:
        return None
    width = width or int(height * 16 / 9)
    lay = LAYOUTS.get(shot.get("clip_type", ""), [(0.80, 0.05), (0.88, 0.05)])
    font = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
    alpha = "if(lt(t,0.6),t/0.6,if(gt(t,4.4),(5-t)/0.6,1))"
    parts = []
    for n, line in enumerate([l.strip() for l in text.split("\n") if l.strip()][:2]):
        y, sz = lay[min(n, len(lay) - 1)]
        px = int(min(height * sz, 0.90 * width / (max(len(line), 1) * 0.60)))   # auto-fit: never wider than 90% of frame
        tf = os.path.join(ctx.audio_dir, f"{tag}_ov{n}.txt")
        with open(tf, "w", encoding="utf-8") as f:
            f.write(line)
        parts.append("drawtext=" + (f"fontfile='{font}':" if font else "") +
                     f"textfile='{tf}':expansion=none:fontcolor=white:fontsize={px}:"
                     f"x=(w-text_w)/2:y=h*{y}:borderw=3:bordercolor=black@0.85:shadowx=2:shadowy=2:alpha='{alpha}'")
    return ",".join(parts)


# ==============================================================================
# STAGE 1: PANEL GENERATION
# ==============================================================================
def get_manifest(ctx):
    run_manifest = os.path.join(ctx.run_dir, "storyboard_manifest.json")
    for path in [run_manifest, "storyboard_manifest.json"]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if path != run_manifest:
                with open(run_manifest, "w", encoding="utf-8") as out_f:
                    json.dump(data, out_f, indent=2, ensure_ascii=False)
            return data
    print(f"❌ No manifest at {run_manifest}")
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


def build_wf1(ctx, shot, idx):
    tag = ctx.shot_tag(idx)
    with open(WF1_PATH, "r") as f:
        wf1 = json.load(f)
    style, anchor, scene = compose_flux_layers(ctx, shot)
    wf1["20"]["inputs"]["value"] = style
    wf1["21"]["inputs"]["value"] = anchor
    wf1["22"]["inputs"]["value"] = scene
    wf1["3"]["inputs"]["latent_image"] = ["5", 0]
    wf1["3"]["inputs"]["denoise"] = 1.0
    wf1["3"]["inputs"]["seed"] = shot.get("seed", 100000 + idx) + ctx.seed_offset
    wf1["9"]["inputs"]["filename_prefix"] = f"projects/{ctx.project_name}/{ctx.run_name}/panels/{tag}"
    return sanitize_wf1(wf1, is_img2img=False)


def stage1_generate_panels(ctx, wait_for_completion=True):
    manifest = get_manifest(ctx)
    if not manifest:
        return
    print(f"\n🚀 [STAGE 1] Project: {ctx.project_name} | Run: {ctx.run_id} | {len(manifest)} shots...")
    fresh = starts_fresh(manifest)
    prev_last_frame = None if fresh else ctx.get_previous_run_last_frame()
    if fresh:
        print("🆕 Shot 1 is flagged continue_from_previous=false -> generated from its own prompt.")
    elif prev_last_frame:
        print(f"🔗 Found previous run's final frame: {prev_last_frame}")
    for i, shot in enumerate(manifest):
        idx = shot.get("shot_index", i + 1)
        tag = ctx.shot_tag(idx)
        if not needs_generation(shot):
            print(f"⏭️  [{tag}] {skip_reason(shot)}")
            continue
        if not ctx.shot_selected(idx):
            continue
        existing = find_files(ctx.panels_dir, tag, "png")
        if existing and not ctx.force:
            print(f"⏭️  [{tag}] panel already exists ({os.path.basename(existing[-1])}) - use --force to re-roll")
            continue
        if i == 0 and prev_last_frame:
            dest = os.path.join(ctx.panels_dir, f"{tag}_00001_.png")
            shutil.copy(prev_last_frame, dest)
            print(f"✅ [{tag}] inherited last frame of run {ctx.run_id - 1}")
            continue
        wf1 = build_wf1(ctx, shot, idx)
        leaks = find_leaks(ctx, wf1["20"]["inputs"]["value"] + " " + wf1["21"]["inputs"]["value"] + " " + wf1["22"]["inputs"]["value"])
        if leaks:
            print(f"⚠️ [{tag}] invented names in the image prompt (model can't use them): {leaks}")
        res = queue_prompt(wf1)
        if res:
            print(f"✅ Queued Panel [{tag}] -> Prompt ID: {res.get('prompt_id')}")
        time.sleep(0.2 if not DRY_RUN else 0)
    if wait_for_completion:
        wait_for_queue_to_finish()


# ==============================================================================
# STAGE 2: SEQUENTIAL VIDEO GENERATION (WAN 2.1 I2V)
# ==============================================================================
def build_fast_wf2(ctx, start_image_filename, motion_prompt, seed, output_prefix):
    with open(WF2_PATH, "r") as f:
        wf2 = json.load(f)
    wf2["10"] = {"inputs": {"image": start_image_filename, "upload": "image"},
                 "class_type": "LoadImage", "_meta": {"title": "Load Start Frame PNG"}}
    if ctx.wan_resolution == "480p":
        wf2["14"]["inputs"]["width"] = 832
        wf2["14"]["inputs"]["height"] = 480
    else:
        wf2["14"]["inputs"]["width"] = 1280
        wf2["14"]["inputs"]["height"] = 720
    wf2["12"]["inputs"]["text"] = motion_prompt          # already contains the (short) character descriptors
    wf2["15"]["inputs"]["seed"] = seed
    wf2["17"]["inputs"]["filename_prefix"] = output_prefix
    loras = get_available_loras()
    speed_lora = "wan2.1_i2v_720p_lightx2v_4step_lora.safetensors"
    if ctx.use_speed_lora and speed_lora in loras:
        wf2["99"] = {"inputs": {"lora_name": speed_lora, "strength_model": 1.0, "model": ["1", 0]},
                     "class_type": "LoraLoaderModelOnly", "_meta": {"title": "Wan 2.1 4-Step Speed LoRA"}}
        wf2["15"]["inputs"]["model"] = ["99", 0]
        wf2["15"]["inputs"]["steps"] = ctx.lora_steps
        wf2["15"]["inputs"]["cfg"] = 1.0
    else:
        wf2["15"]["inputs"]["steps"] = ctx.wan_steps
        wf2["15"]["inputs"]["cfg"] = 5.0
    return wf2


def stage2_generate_videos(ctx, wait_for_completion=True):
    manifest = get_manifest(ctx)
    if not manifest:
        return
    os.makedirs(COMFYUI_INPUT_DIR, exist_ok=True)
    os.makedirs(ctx.videos_dir, exist_ok=True)
    os.makedirs(ctx.chain_dir, exist_ok=True)
    loras = get_available_loras()
    using_lora = ctx.use_speed_lora and ("wan2.1_i2v_720p_lightx2v_4step_lora.safetensors" in loras)
    eff_steps = ctx.lora_steps if using_lora else ctx.wan_steps
    print(f"\n🎥 [STAGE 2] {len(manifest)} shots | Res: {ctx.wan_resolution} | Steps: {eff_steps} (Speed LoRA: {using_lora})")
    fresh = starts_fresh(manifest)
    prev_run_frame = None if fresh else ctx.get_previous_run_last_frame()
    chain_frame = None
    for i, shot in enumerate(manifest):
        idx = shot.get("shot_index", i + 1)
        tag = ctx.shot_tag(idx)
        if not needs_generation(shot):
            print(f"⏭️  [{tag}] {skip_reason(shot)}")
            continue
        if not ctx.shot_selected(idx):
            continue
        existing = find_files(ctx.videos_dir, tag, "mp4")
        if existing and not ctx.force:
            print(f"⏭️  [{tag}] video already exists - use --force to re-render")
            if ctx.chain_within_run:
                chain_frame = os.path.join(ctx.chain_dir, f"{tag}_last.png")
                if not extract_last_frame(existing[-1], chain_frame):
                    chain_frame = None
            continue
        start_png = None
        if i == 0 and prev_run_frame and os.path.exists(prev_run_frame):
            start_png = prev_run_frame
            print(f"🔗 [{tag}] using final frame of run {ctx.run_id - 1} for seamless continuation")
        elif ctx.chain_within_run and i > 0 and shot.get("continue_from_previous") is True and chain_frame and os.path.exists(chain_frame):
            start_png = chain_frame
            print(f"🔗 [{tag}] chaining last frame of the previous shot (same scene)")
        else:
            matches = find_files(ctx.panels_dir, tag, "png")
            if matches:
                start_png = matches[-1]
        if not start_png or not os.path.exists(start_png):
            print(f"⚠️ Missing start panel for {tag} in {ctx.panels_dir}. Skipping...")
            continue
        input_filename = f"start_{tag}.png"
        if not DRY_RUN:
            shutil.copy(start_png, os.path.join(COMFYUI_INPUT_DIR, input_filename))
        output_prefix = f"projects/{ctx.project_name}/{ctx.run_name}/videos/{tag}"
        wf2 = build_fast_wf2(ctx, input_filename, compose_wan_prompt(ctx, shot), shot.get("seed", 90000 + idx) + ctx.seed_offset, output_prefix)
        res = queue_prompt(wf2)
        if res:
            print(f"🎞️ Queued Video [{tag}.mp4] -> Prompt ID: {res.get('prompt_id')}")
        if ctx.chain_within_run and not DRY_RUN:
            wait_for_queue_to_finish()
            rendered = find_files(ctx.videos_dir, tag, "mp4")
            chain_frame = None
            if rendered:
                cf = os.path.join(ctx.chain_dir, f"{tag}_last.png")
                if extract_last_frame(rendered[-1], cf):
                    chain_frame = cf
    if wait_for_completion and not ctx.chain_within_run:
        wait_for_queue_to_finish()
    all_mp4s = sorted(glob.glob(os.path.join(ctx.videos_dir, "*.mp4")), key=nkey)
    if all_mp4s:
        run_last_frame = os.path.join(ctx.final_dir, f"{ctx.project_name}_run{ctx.run_id}_last_frame.png")
        if extract_last_frame(all_mp4s[-1], run_last_frame):
            ctx.save_project_meta(last_frame_path=run_last_frame)
            print(f"💾 Saved final frame of run {ctx.run_id}: {run_last_frame}")


# ==============================================================================
# STAGE 3: AUDIO + OVERLAYS + MUXING + RUN STITCHING
# ==============================================================================
def resolve_source_video(ctx, shot, tag, ref_size):
    """(path, kind) of the picture for a shot: rendered clip, re-used clip or external clip"""
    ext = external_clip(shot)
    if ext:
        src = ext if os.path.isabs(ext) else os.path.join(ctx.project_dir, ext)
        if not os.path.exists(src):
            return None, "external clip missing: " + src
        out = os.path.join(ctx.final_dir, f"{tag}_ext.mp4")
        w, h = ref_size
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps=16"
        if has_audio_stream(src):
            cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf, "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", out]
        else:
            cmd = ["ffmpeg", "-y", "-i", src, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-vf", vf,
                   "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-shortest", out]
        run_ff(cmd)
        return (out, "external") if os.path.exists(out) else (None, "could not normalize external clip")
    rs = reuse_spec(shot)
    if rs:
        src_dir = os.path.join(ctx.project_dir, f"run{rs['run']}", "videos")
        src_tag = f"{ctx.project_name}_run{rs['run']}_shot{rs['shot']}"
        files = find_files(src_dir, src_tag, "mp4")
        return (files[-1], "reuse") if files else (None, f"source clip not rendered yet: run{rs['run']}/shot{rs['shot']}")
    files = find_files(ctx.videos_dir, tag, "mp4")
    return (files[-1], "render") if files else (None, "no rendered video")


def mux_clip(ctx, shot, tag, raw_mp4, audio_m4a, out_mp4, kind):
    if kind == "external":                       # already has final audio + normalized video
        shutil.copy(raw_mp4, out_mp4)
        return os.path.exists(out_mp4)
    w, h = probe_size(raw_mp4)
    vf = overlay_filter(ctx, shot, tag, h, w)
    base = ["ffmpeg", "-y", "-i", raw_mp4, "-i", audio_m4a, "-map", "0:v:0", "-map", "1:a:0"]
    tail = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-shortest", out_mp4]
    if vf:
        r = run_ff(base + ["-vf", vf, "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p"] + tail)
        if r.returncode == 0 and os.path.exists(out_mp4):
            return True
        print(f"⚠️ [{tag}] text overlay failed ({r.stderr.decode(errors='replace')[-200:].strip()}) - muxing without text")
    r = run_ff(base + ["-c:v", "copy"] + tail)
    return r.returncode == 0 and os.path.exists(out_mp4)


def natural_runs_in_project(ctx):
    dirs = [d for d in glob.glob(os.path.join(ctx.project_dir, "run*")) if re.fullmatch(r"run\d+", os.path.basename(d))]
    return sorted(dirs, key=nkey)


def stage3_audio_and_stitch(ctx):
    manifest = get_manifest(ctx)
    if not manifest:
        return
    print(f"\n🔊 [STAGE 3] Audio + overlays + stitch for {ctx.project_name} run {ctx.run_id}...")
    os.makedirs(ctx.audio_dir, exist_ok=True)
    os.makedirs(ctx.final_dir, exist_ok=True)
    live = [s for s in manifest if not is_placeholder(s)]
    ref_size = (1280, 720)
    for s in live:
        p, k = resolve_source_video(ctx, s, ctx.shot_tag(s.get("shot_index", 1)), ref_size) if not external_clip(s) else (None, "")
        if p:
            ref_size = probe_size(p)
            break
    muxed = []
    prev_cue, cursor = None, 0.0
    for pos, shot in enumerate(live):
        idx = shot.get("shot_index", pos + 1)
        tag = ctx.shot_tag(idx)
        raw, kind = resolve_source_video(ctx, shot, tag, ref_size)
        if not raw:
            print(f"⚠️ [{tag}] {kind} - skipped")
            continue
        cue = shot.get("bgm_cue") or ""
        if cue != prev_cue:
            prev_cue, cursor, first = cue, 0.0, True
        else:
            first = False
        nxt = live[pos + 1].get("bgm_cue", "") if pos + 1 < len(live) else None
        last = (nxt != cue)
        bgm = None
        if kind != "external":
            bpath = resolve_bgm_file(ctx, cue, shot.get("bgm_prompt", ""))
            if bpath:
                dur = _bgm_dur_cache.setdefault(bpath, probe_duration(bpath) or 90.0)
                bgm = {"path": bpath, "offset": cursor % dur, "fade_in": first, "fade_out": last}
            cursor += 5.0
            audio = generate_shot_audio(ctx, shot, bgm)
        else:
            audio = None
        out = os.path.join(ctx.final_dir, f"{tag}_muxed.mp4")
        if mux_clip(ctx, shot, tag, raw, audio, out, kind):
            muxed.append(out)
            print(f"   ✔ {os.path.basename(out)}  [{kind}{', music ' + cue if bgm else ''}]")
        else:
            print(f"⚠️ [{tag}] mux failed")
    if not muxed:
        print("❌ No clips available to stitch!")
        return
    master = os.path.join(ctx.final_dir, f"{ctx.project_name}_run{ctx.run_id}_master.mp4")
    if concat_videos(muxed, master):
        print(f"\n🎬 Master run video: {master}")
    else:
        print("❌ stitching failed")
        return
    run_last_frame = os.path.join(ctx.final_dir, f"{ctx.project_name}_run{ctx.run_id}_last_frame.png")
    if extract_last_frame(master, run_last_frame):
        ctx.save_project_meta(last_frame_path=run_last_frame)
    masters = []
    for rd in natural_runs_in_project(ctx):
        n = os.path.basename(rd)[3:]
        m = os.path.join(rd, "final", f"{ctx.project_name}_run{n}_master.mp4")
        if os.path.exists(m):
            masters.append(m)
    if len(masters) > 1:
        proj_master = os.path.join(ctx.project_dir, f"{ctx.project_name}_COMPLETE_SERIES.mp4")
        if concat_videos(masters, proj_master):
            print(f"🌟 Complete series (natural run order) updated: {proj_master}")


# ==============================================================================
# EPISODES (stack runs into episode videos, append credits if supplied)
# ==============================================================================
def load_episode_map(ctx):
    p = os.path.join(ctx.project_dir, "episode_map.json")
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for k, v in raw.items():
        out[int(k)] = v if isinstance(v, dict) else {"title": "", "runs": v}
    return out


def build_episode_videos(ctx):
    emap = load_episode_map(ctx)
    if not emap:
        print("❌ no episode_map.json in the project folder")
        return
    os.makedirs(ctx.episodes_dir, exist_ok=True)
    built = []
    for ep in sorted(emap):
        files = []
        for r in sorted(emap[ep]["runs"]):
            m = os.path.join(ctx.project_dir, f"run{r}", "final", f"{ctx.project_name}_run{r}_master.mp4")
            if os.path.exists(m):
                files.append(m)
            else:
                print(f"   (episode {ep}: run{r} master not rendered yet)")
        cred = os.path.join(ctx.project_dir, "credits", f"ep{ep}_credits.mp4")
        if os.path.exists(cred):
            files.append(cred)
        if not files:
            continue
        out = os.path.join(ctx.episodes_dir, f"{ctx.project_name}_EP{ep:02d}.mp4")
        if concat_videos(files, out):
            built.append(out)
            print(f"📺 Episode {ep} ({emap[ep].get('title', '')}): {out}")
    if len(built) > 1:
        series = os.path.join(ctx.episodes_dir, f"{ctx.project_name}_SERIES.mp4")
        if concat_videos(built, series):
            print(f"🌟 Full series: {series}")


# ==============================================================================
# PROMPT PREVIEW (no GPU) - exactly what Flux / Wan / TTS / music will receive
# ==============================================================================
def export_prompt_preview(ctx, run_ids=None):
    emap = load_episode_map(ctx)
    ep_of = {r: ep for ep, v in emap.items() for r in v["runs"]}
    runs = run_ids or sorted(ep_of) or [ctx.run_id]
    lines = [f"# Prompt preview - {ctx.project_name}", "",
             "Generated by `run_cli_studio.py`. These are the exact text layers sent to the models.", ""]
    warnings = []
    max_words = 0
    for r in runs:
        c = StudioContext(ctx.project_name, r)
        manifest = get_manifest(c)
        lines += [f"## Run {r}" + (f" (episode {ep_of[r]})" if r in ep_of else ""), ""]
        for i, shot in enumerate(manifest):
            idx = shot.get("shot_index", i + 1)
            tag = c.shot_tag(idx)
            lines.append(f"### {tag}  - {shot.get('clip_type', 'story')} - scene {shot.get('scene_ref', '')}")
            if not needs_generation(shot):
                lines += [f"*{skip_reason(shot)}*", ""]
                if shot.get("overlay_text"): lines += [f"- overlay: `{shot['overlay_text']}`"]
                if shot.get("dialogue"): lines += [f"- narration: {shot['dialogue']}"]
                lines.append("")
                continue
            style, anchor, scene = compose_flux_layers(c, shot)
            wan = compose_wan_prompt(c, shot)
            w = len((style + " " + anchor + " " + scene).split())
            max_words = max(max_words, w)
            leaks = find_leaks(c, style + " " + anchor + " " + scene + " " + wan)
            if leaks: warnings.append(f"{tag}: invented names in image prompt: {leaks}")
            if w > 300: warnings.append(f"{tag}: Flux prompt is {w} words (very long - trim the action text)")
            if len((shot.get('dialogue') or '').split()) > 14: warnings.append(f"{tag}: dialogue > 14 words")
            cont = ""
            if i == 0 and r > 1:
                cont = "continues from previous run's last frame" if shot.get("continue_from_previous") else "fresh panel"
            voice, rate, pitch = resolve_voice(c, shot)
            lines += [f"- **Flux** ({w} words): {style}. {anchor}. {scene}",
                      f"- **Wan**: {wan}",
                      f"- **Voice** `{voice}`" + (f" rate {rate}" if rate else "") + (f" pitch {pitch}" if pitch else "") +
                      f": {tts_text(c, shot.get('dialogue', '')) or '(no dialogue)'}",
                      f"- **SFX** `{shot.get('sfx_type')}` | **Music** `{shot.get('bgm_cue')}`" + (f" | {cont}" if cont else ""), ""]
    lines += ["## Checks", f"- longest Flux prompt: {max_words} words", f"- warnings: {len(warnings)}"]
    lines += [f"  - {w}" for w in warnings]
    out = os.path.join(ctx.project_dir, "prompts_preview.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"📝 Prompt preview written: {out}  (warnings: {len(warnings)}, longest Flux prompt {max_words} words)")
    return out


# ==============================================================================
# DRIVERS
# ==============================================================================
def run_stage(ctx, stage):
    if stage in ("all", "panels"): stage1_generate_panels(ctx)
    if stage in ("all", "videos"): stage2_generate_videos(ctx)
    if stage in ("all", "audio"): stage3_audio_and_stitch(ctx)


def cli_main(args):
    global DRY_RUN
    DRY_RUN = bool(args.dry_run)
    runs = parse_ranges(args.runs) if args.runs else [1]
    base = StudioContext(args.project, runs[0])
    if args.stage == "episodes":
        build_episode_videos(base); return
    if args.stage == "preview":
        export_prompt_preview(base, parse_ranges(args.runs) if args.runs else None); return
    for r in runs:
        ctx = StudioContext(args.project, r)
        ctx.force = args.force
        if args.shots: ctx.only_shots = set(parse_ranges(args.shots))
        if getattr(args, "steps", None): ctx.lora_steps = args.steps
        ctx.seed_offset = getattr(args, "reseed", 0) or 0
        if args.res: ctx.wan_resolution = "480p" if args.res == 480 else "720p"
        if args.chain: ctx.chain_within_run = True
        run_stage(ctx, args.stage)
    if args.stage == "all" and len(runs) > 1:
        build_episode_videos(base)


def interactive_menu():
    global DRY_RUN
    ctx = StudioContext(DEFAULT_PROJECT, 1)
    while True:
        print("\n==================================================================")
        print("🎬 COMIC-TO-VIDEO CLI PRODUCTION STUDIO (v6 - bibles edition)")
        print("==================================================================")
        print(f"📂 Active Project : {ctx.project_name} (Run: run{ctx.run_id})   bibles: {'yes' if ctx.bibles else 'NO'}")
        print(f"🏷️ Shot Naming    : {ctx.shot_tag(1)} ... {ctx.shot_tag(10)}")
        print(f"⚡ Video Settings : {ctx.wan_resolution} | Steps: {ctx.wan_steps} | Speed LoRA: {ctx.use_speed_lora} | Force re-render: {ctx.force}")
        print(f"🔗 Intra-Run Chain: {'ON (same-scene shots)' if ctx.chain_within_run else 'OFF (run->run continuity follows continue_from_previous)'}")
        print("------------------------------------------------------------------")
        print("1. Run Full Pipeline for this run (Panels -> Videos -> Audio & Stitch)")
        print("2. Stage 1 Only: Generate Panels")
        print("3. Stage 2 Only: Generate Videos (Wan 2.1 I2V)")
        print("4. Stage 3 Only: Voice / SFX / Music / Overlays & Stitch run")
        print("5. Switch Project Name / Run")
        print("6. Settings (resolution, steps, chaining, force, only-shots)")
        print("7. Exit")
        print("8. Build EPISODE videos (stack runs; appends credits/epN_credits.mp4)")
        print("9. Export prompt preview (no GPU) -> prompts_preview.md")
        print("10. Run ALL runs in the episode map end-to-end, then build episodes")
        print("==================================================================")
        choice = input("Select an option (1-10): ").strip()
        if choice == "1": run_stage(ctx, "all")
        elif choice == "2": stage1_generate_panels(ctx)
        elif choice == "3": stage2_generate_videos(ctx)
        elif choice == "4": stage3_audio_and_stitch(ctx)
        elif choice == "5":
            new_p = input(f"Project name (current: {ctx.project_name}): ").strip() or ctx.project_name
            new_r = input(f"Run number (current: {ctx.run_id}): ").strip() or str(ctx.run_id)
            ctx = StudioContext(project_name=new_p, run_id=int(new_r))
            print(f"✅ Switched to '{ctx.project_name}', run {ctx.run_id}")
        elif choice == "6":
            res_in = input("Resolution? (1 = 720p, 2 = 480p [3x faster]): ").strip()
            if res_in == "2": ctx.wan_resolution = "480p"
            elif res_in == "1": ctx.wan_resolution = "720p"
            steps_in = input(f"Sampling steps without Speed LoRA (current {ctx.wan_steps}): ").strip()
            if steps_in.isdigit(): ctx.wan_steps = int(steps_in)
            ctx.use_speed_lora = (input("Use 4-step Speed LoRA if present? (Y/n): ").strip().lower() != "n")
            ctx.chain_within_run = (input("Chain last frame inside the same scene? (y/N): ").strip().lower() == "y")
            ctx.force = (input("Re-render even if panel/video exists? (y/N): ").strip().lower() == "y")
            sh = input("Only these shots (e.g. 3,5-7; blank = all): ").strip()
            ctx.only_shots = set(parse_ranges(sh)) if sh else None
        elif choice == "7":
            print("Exiting. Goodbye!"); break
        elif choice == "8": build_episode_videos(ctx)
        elif choice == "9": export_prompt_preview(ctx)
        elif choice == "10":
            emap = load_episode_map(ctx)
            for r in sorted(r for v in emap.values() for r in v["runs"]):
                c = StudioContext(ctx.project_name, r)
                c.wan_resolution, c.wan_steps, c.use_speed_lora = ctx.wan_resolution, ctx.wan_steps, ctx.use_speed_lora
                c.lora_steps = ctx.lora_steps
                c.chain_within_run, c.force = ctx.chain_within_run, ctx.force
                run_stage(c, "all")
            build_episode_videos(ctx)
        else:
            print("❌ Invalid option.")


def main():
    ap = argparse.ArgumentParser(description="Comic-to-Video production studio (non-interactive mode)")
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--runs", help="e.g. 1-10 or 3,4")
    ap.add_argument("--stage", choices=["all", "panels", "videos", "audio", "episodes", "preview"])
    ap.add_argument("--shots", help="only these shot indexes, e.g. 3,5-7")
    ap.add_argument("--force", action="store_true", help="re-render even if output exists")
    ap.add_argument("--dry-run", action="store_true", help="queue nothing; print what would be queued")
    ap.add_argument("--res", type=int, choices=[480, 720])
    ap.add_argument("--reseed", type=int, default=0, help="add N to every seed (use with --force --shots to re-roll a bad panel/clip)")
    ap.add_argument("--steps", type=int, choices=range(3, 13), metavar="3-12", help="Wan sampler steps with the speed LoRA (default 6; 4 is faster)")
    ap.add_argument("--chain", action="store_true", help="chain last frame inside the same scene")
    args = ap.parse_args()
    if args.stage:
        cli_main(args)
    else:
        interactive_menu()


if __name__ == "__main__":
    main()
