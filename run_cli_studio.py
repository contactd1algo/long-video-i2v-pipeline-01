import os
import json
import urllib.request
import time
import glob

COMFYUI_HOST = "http://127.0.0.1:8188"
COMFYUI_OUTPUT_DIR = "/workspace/ComfyUI/output"
COMFYUI_INPUT_DIR = "/workspace/ComfyUI/input"
EPISODE_ID = "ep01"

WF1_PATH = "workflow1_panel_generator_and_tweaker.json"
WF2_PATH = "workflow2_sequential_i2v_wan21.json"

PANELS_DIR = os.path.join(COMFYUI_OUTPUT_DIR, f"episodes/{EPISODE_ID}/panels")
VIDEOS_DIR = os.path.join(COMFYUI_OUTPUT_DIR, f"episodes/{EPISODE_ID}/videos")

def queue_prompt(workflow_dict):
    url = f"{COMFYUI_HOST}/prompt"
    payload = json.dumps({"prompt": workflow_dict}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"❌ Connection error to ComfyUI at {COMFYUI_HOST}: {e}")
        return None

def print_banner():
    print("==================================================")
    print("🎬 COMIC-TO-VIDEO CLI PRODUCTION STUDIO")
    print("==================================================")
    print("1. Generate All Sequential Clean Panels (Stage 1)")
    print("2. Regenerate / Tweak a Single Panel")
    print("3. Generate Sequential Videos from Panels (Stage 2)")
    print("4. Exit")
    print("==================================================")

def get_manifest():
    # Default manifest or load from a JSON file if present
    manifest_path = "storyboard_manifest.json"
    if os.path.exists(manifest_path):
        with open(manifest_path, "r") as f:
            return json.load(f)
    
    # Default sample manifest
    return [
        {
            "shot_index": 1,
            "shot_type": "wide establishing shot",
            "action": "Arjun walking down a dim industrial server corridor holding an orange diagnostic tablet",
            "lighting": "cyan rim lighting, blinking blue server LEDs",
            "motion_prompt": "Maintain 2D cel-shaded graphic novel style. Slow camera push-in. Arjun walks forward steadily, tablet screen glowing softly, blue server rack lights blinking in background.",
            "seed": 104820
        },
        {
            "shot_index": 2,
            "shot_type": "medium close-up",
            "action": "Arjun plugging a bypass cable into a rack terminal, focused tense expression",
            "lighting": "amber screen reflection on his face, sharp shadows",
            "motion_prompt": "Maintain 2D cel-shaded graphic novel style. Subtle handheld camera drift. Arjun pushes the connector in, blinks, and looks up at the terminal monitor.",
            "seed": 104821
        }
    ]

def stage1_generate_panels():
    if not os.path.exists(WF1_PATH):
        print(f"❌ Missing workflow file: {WF1_PATH}")
        return

    with open(WF1_PATH, "r") as f:
        wf1 = json.load(f)

    manifest = get_manifest()
    print(f"\n🚀 Starting Stage 1: Generating {len(manifest)} clean panels...")

    style_prefix = "clean 2D graphic novel illustration, modern comic art style, crisp ink lineart, flat vibrant cel-shading, 16:9 cinematic framing, no borders, uncropped"
    char_anchor = "Arjun, 30yo male engineer, short black hair, light beard, dark olive tactical jacket over charcoal crewneck"

    for shot in manifest:
        idx = shot.get("shot_index", 1)
        seq_name = f"shot_{idx:04d}"

        wf1["20"]["inputs"]["value"] = style_prefix
        wf1["21"]["inputs"]["value"] = char_anchor
        wf1["22"]["inputs"]["value"] = f"{shot.get('shot_type', '')}, {shot.get('action', '')}, {shot.get('lighting', '')}".strip(", ")
        
        wf1["3"]["inputs"]["latent_image"] = ["5", 0]
        wf1["3"]["inputs"]["denoise"] = 1.0
        wf1["3"]["inputs"]["seed"] = shot.get("seed", 100000 + idx)
        wf1["9"]["inputs"]["filename_prefix"] = f"episodes/{EPISODE_ID}/panels/{seq_name}"

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
    new_action = input("Enter tweaked action prompt: ").strip()
    new_seed = int(input("Enter new seed (default 104825): ") or "104825")
    tweak_mode = input("Mode? (1 = Full Re-roll, 2 = Img2Img Refinement): ").strip()

    with open(WF1_PATH, "r") as f:
        wf1 = json.load(f)

    wf1["20"]["inputs"]["value"] = "clean 2D graphic novel illustration, modern comic art style, crisp ink lineart, flat vibrant cel-shading, 16:9 cinematic framing, no borders, uncropped"
    wf1["21"]["inputs"]["value"] = "Arjun, 30yo male engineer, short black hair, light beard, dark olive tactical jacket over charcoal crewneck"
    wf1["22"]["inputs"]["value"] = new_action
    wf1["3"]["inputs"]["seed"] = new_seed
    wf1["9"]["inputs"]["filename_prefix"] = f"episodes/{EPISODE_ID}/panels/{shot_label}"

    if tweak_mode == "2":
        os.makedirs(COMFYUI_INPUT_DIR, exist_ok=True)
        temp_input = f"tweak_{shot_label}.png"
        import shutil
        shutil.copy(matching[-1], os.path.join(COMFYUI_INPUT_DIR, temp_input))
        wf1["30"]["inputs"]["image"] = temp_input
        wf1["3"]["inputs"]["latent_image"] = ["31", 0]
        wf1["3"]["inputs"]["denoise"] = 0.55
        print("🛠️ Running Img2Img Refinement pass...")
    else:
        wf1["3"]["inputs"]["latent_image"] = ["5", 0]
        wf1["3"]["inputs"]["denoise"] = 1.0
        print("🛠️ Running Full Re-roll pass...")

    res = queue_prompt(wf1)
    if res:
        print(f"✅ Queued tweak for [{shot_label}] -> Prompt ID: {res.get('prompt_id')}")

def stage2_generate_videos():
    if not os.path.exists(WF2_PATH):
        print(f"❌ Missing workflow file: {WF2_PATH}")
        return

    with open(WF2_PATH, "r") as f:
        wf2 = json.load(f)

    manifest = get_manifest()
    print(f"\n🎥 Starting Stage 2: Generating sequential videos for {len(manifest)} shots...")

    for i, shot in enumerate(manifest):
        idx = shot.get("shot_index", i + 1)
        seq_clip_name = f"clip_{idx:04d}"
        motion_prompt = shot.get("motion_prompt", "Maintain 2D comic style. Subtle motion.")

        wf2["10"]["inputs"]["directory"] = PANELS_DIR
        wf2["10"]["inputs"]["start_index"] = i
        wf2["10"]["inputs"]["image_load_cap"] = 1
        wf2["10"]["inputs"]["sort_method"] = "numerical"

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
