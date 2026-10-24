import os
import json
import urllib.request
import urllib.error
import time
import glob
import shutil

COMFYUI_HOST = "http://127.0.0.1:8188"

def detect_comfy_dir():
    for candidate in ["/workspace/runpod-slim/ComfyUI", "/workspace/ComfyUI", "/ComfyUI", "/opt/ComfyUI"]:
        if os.path.isdir(candidate):
            return candidate
    return "/workspace/runpod-slim/ComfyUI"

COMFYUI_DIR = detect_comfy_dir()
COMFYUI_OUTPUT_DIR = os.path.join(COMFYUI_DIR, "output")
COMFYUI_INPUT_DIR = os.path.join(COMFYUI_DIR, "input")
EPISODE_ID = "ep01"

WF1_PATH = "workflow1_panel_generator_and_tweaker.json"
WF2_PATH = "workflow2_sequential_i2v_wan21.json"

PANELS_DIR = os.path.join(COMFYUI_OUTPUT_DIR, f"episodes/{EPISODE_ID}/panels")
VIDEOS_DIR = os.path.join(COMFYUI_OUTPUT_DIR, f"episodes/{EPISODE_ID}/videos")

STYLE_PREFIX = "clean 2D graphic novel illustration, modern comic art style, crisp ink lineart, flat vibrant cel-shading, bright studio lighting, well-lit scene, vivid colors, high visibility, 16:9 cinematic framing, no borders, uncropped"
CHAR_ANCHOR = "Arjun, 30yo male engineer, short black hair, light beard, dark olive tactical jacket over charcoal crewneck"

def get_available_loras():
    lora_dir = os.path.join(COMFYUI_DIR, "models", "loras")
    if not os.path.isdir(lora_dir):
        return []
    valid = []
    for f in os.listdir(lora_dir):
        if f.endswith(".safetensors"):
            full_p = os.path.join(lora_dir, f)
            if os.path.getsize(full_p) > 1_000_000:
                valid.append(f)
    return valid

def sanitize_wf1_for_execution(wf1, is_img2img=False):
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

    # Flux.1-dev uses distilled guidance; KSampler CFG > 1.0 causes severe over-contrast/darkening
    if "3" in wf1 and "1" in wf1 and "flux" in wf1["1"].get("inputs", {}).get("ckpt_name", "").lower():
        wf1["3"]["inputs"]["cfg"] = 1.0

    if not is_img2img:
        wf1.pop("30", None)
        wf1.pop("31", None)

    return wf1

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

def print_banner():
    print("==================================================")
    print("🎬 COMIC-TO-VIDEO CLI PRODUCTION STUDIO")
    print(f"📂 ComfyUI Root: {COMFYUI_DIR}")
    print("==================================================")
    print("1. Generate All Sequential Clean Panels (Stage 1)")
    print("2. Regenerate / Tweak a Single Panel (Stage 1.5)")
    print("3. Generate Sequential Videos from Panels (Stage 2)")
    print("4. Exit")
    print("==================================================")

def get_manifest():
    for path in ["storyboard_manifest.json", "sample_storyboard_manifest.json"]:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    return []

def stage1_generate_panels():
    if not os.path.exists(WF1_PATH):
        print(f"❌ Missing workflow file: {WF1_PATH}")
        return

    os.makedirs(PANELS_DIR, exist_ok=True)
    existing = glob.glob(os.path.join(PANELS_DIR, "shot_*.png"))
    if existing:
        ans = input(f"⚠️ Found {len(existing)} existing panel PNGs in {PANELS_DIR}. Delete them before generating new ones so Stage 2 indexes stay clean? (y/N): ").strip().lower()
        if ans == "y":
            for p in existing:
                os.remove(p)
            print("🧹 Cleared old panel PNGs.")

    manifest = get_manifest()
    print(f"\n🚀 Starting Stage 1: Generating {len(manifest)} well-lit clean panels...")

    for shot in manifest:
        with open(WF1_PATH, "r") as f:
            wf1 = json.load(f)

        idx = shot.get("shot_index", 1)
        seq_name = f"shot_{idx:04d}"

        wf1["20"]["inputs"]["value"] = STYLE_PREFIX
        wf1["21"]["inputs"]["value"] = CHAR_ANCHOR
        lighting_str = shot.get("lighting", "bright cinematic lighting, well-lit scene")
        wf1["22"]["inputs"]["value"] = f"{shot.get('shot_type', '')}, {shot.get('action', '')}, {lighting_str}, bright well-lit comic art, clear visibility".strip(", ")
        
        wf1["3"]["inputs"]["latent_image"] = ["5", 0]
        wf1["3"]["inputs"]["denoise"] = 1.0
        wf1["3"]["inputs"]["seed"] = shot.get("seed", 100000 + idx)
        wf1["9"]["inputs"]["filename_prefix"] = f"episodes/{EPISODE_ID}/panels/{seq_name}"

        wf1 = sanitize_wf1_for_execution(wf1, is_img2img=False)

        res = queue_prompt(wf1)
        if res:
            print(f"✅ Queued panel [{seq_name}] -> Prompt ID: {res.get('prompt_id')}")
        time.sleep(0.3)
    print("🎉 All panel generation prompts queued!")

def stage1_5_tweak_panel():
    os.makedirs(PANELS_DIR, exist_ok=True)
    existing_panels = sorted(glob.glob(os.path.join(PANELS_DIR, "shot_*.png")))
    if not existing_panels:
        print("\n⚠️ No generated panels found in", PANELS_DIR)
        return

    print("\nAvailable panels:")
    for p in existing_panels:
        print(f" - {os.path.basename(p)}")

    shot_label = input("Enter shot name to tweak (e.g., shot_0001): ").strip()
    matching = sorted(glob.glob(os.path.join(PANELS_DIR, f"{shot_label}*.png")))
    
    if not matching:
        print(f"❌ No matching panel found for {shot_label}")
        return

    print(f"Found latest render: {matching[-1]}")
    new_action = input("Enter tweaked action/lighting prompt: ").strip()
    new_seed = int(input("Enter new seed (default 104825): ") or "104825")
    tweak_mode = input("Mode? (1 = Full Re-roll, 2 = Img2Img Refinement): ").strip()

    with open(WF1_PATH, "r") as f:
        wf1 = json.load(f)

    wf1["20"]["inputs"]["value"] = STYLE_PREFIX
    wf1["21"]["inputs"]["value"] = CHAR_ANCHOR
    wf1["22"]["inputs"]["value"] = f"{new_action}, bright well-lit comic art, clear visibility"
    wf1["3"]["inputs"]["seed"] = new_seed
    wf1["9"]["inputs"]["filename_prefix"] = f"episodes/{EPISODE_ID}/panels/{shot_label}"

    is_img2img = (tweak_mode == "2")
    if is_img2img:
        os.makedirs(COMFYUI_INPUT_DIR, exist_ok=True)
        temp_input = f"tweak_{shot_label}.png"
        shutil.copy(matching[-1], os.path.join(COMFYUI_INPUT_DIR, temp_input))
        wf1["30"]["inputs"]["image"] = temp_input
        wf1["3"]["inputs"]["latent_image"] = ["31", 0]
        wf1["3"]["inputs"]["denoise"] = 0.55
        print("🛠️ Running Img2Img Refinement pass...")
    else:
        wf1["3"]["inputs"]["latent_image"] = ["5", 0]
        wf1["3"]["inputs"]["denoise"] = 1.0
        print("🛠️ Running Full Re-roll pass...")

    wf1 = sanitize_wf1_for_execution(wf1, is_img2img=is_img2img)

    res = queue_prompt(wf1)
    if res:
        print(f"✅ Queued tweak for [{shot_label}] -> Prompt ID: {res.get('prompt_id')}")

def stage2_generate_videos():
    if not os.path.exists(WF2_PATH):
        print(f"❌ Missing workflow file: {WF2_PATH}")
        return

    os.makedirs(VIDEOS_DIR, exist_ok=True)
    manifest = get_manifest()
    print(f"\n🎥 Starting Stage 2: Generating sequential videos for {len(manifest)} shots...")

    for i, shot in enumerate(manifest):
        with open(WF2_PATH, "r") as f:
            wf2 = json.load(f)

        idx = shot.get("shot_index", i + 1)
        seq_clip_name = f"clip_{idx:04d}"
        motion_prompt = shot.get("motion_prompt", "Maintain 2D comic style. Subtle motion.")

        wf2["10"]["inputs"]["directory"] = PANELS_DIR
        wf2["10"]["inputs"]["start_index"] = i
        wf2["10"]["inputs"]["image_load_cap"] = 1
        wf2["10"]["inputs"]["sort_method"] = "Numerical (ASC)"

        wf2["12"]["inputs"]["text"] = motion_prompt
        wf2["15"]["inputs"]["seed"] = shot.get("seed", 90000 + idx)
        wf2["17"]["inputs"]["filename_prefix"] = f"episodes/{EPISODE_ID}/videos/{seq_clip_name}"

        res = queue_prompt(wf2)
        if res:
            print(f"🎞️ Queued Video [{seq_clip_name}.mp4] from start_index={i} -> Prompt ID: {res.get('prompt_id')}")
        time.sleep(0.3)
    print("🎉 All video generation prompts queued!")

def main():
    while True:
        print_banner()
        choice = input("Select an option (1-4): ").strip()
        if choice == "1":
            stage1_generate_panels()
        elif choice == "2":
            stage1_5_tweak_panel()
        elif choice == "3":
            stage2_generate_videos()
        elif choice == "4":
            print("Exiting CLI Studio. Goodbye!")
            break
        else:
            print("❌ Invalid option. Please choose 1-4.")
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    main()
