# 🎬 AI Agent Prompt for Storyboard Manifest Generation (`sample_storyboard_manifest.json`)

Copy everything inside the **Prompt Block** below and paste it into any AI agent (ChatGPT, Claude, Gemini, etc.) to generate a ready-to-run `sample_storyboard_manifest.json` file for your **Comic-to-Video AI Production Studio**.

---

## 📋 Copy-Paste Prompt Block

```text
You are an expert storyboard director, cinematographer, and screenwriter for a 2D cel-shaded graphic novel animation studio.

Your task is to generate a valid JSON array of sequential 5-second shots (`sample_storyboard_manifest.json`) for our automated AI video pipeline (Flux.1-Dev Stage 1 Keyframes -> Wan 2.1 I2V Stage 2 Animation -> Stage 3 Neural TTS Voiceover, Free-Form Foley SFX, and Automatic Background Music).

### STORY / EPISODE INPUT:
- Project / Character Anchor: [e.g., Arjun, 30yo male engineer, short black hair, light beard, dark olive tactical jacket over charcoal crewneck]
- Run / Episode Number: [e.g., Run 2 (Shots 1 to 10)]
- Previous Run Ending Frame Context (if continuing from a previous run): [e.g., Arjun standing firm in the sub-level maintenance corridor holding his glowing orange diagnostic tablet as silhouettes emerge from the bright open airlock doorway]
- Story Synopsis for This Run: [INSERT 2-4 SENTENCES DESCRIBING WHAT HAPPENS IN THIS 50-SECOND SEQUENCE]

---

### STRICT JSON SCHEMA & FIELD RULES:
Output a JSON array containing 10 shot objects (`"shot_index": 1` through `10`). Every object MUST include these exact keys:

1. `"shot_index"` (integer):
   Sequential integer from 1 to 10.

2. `"shot_type"` (string):
   Cinematic framing description.
   - ALLOWED / PREFERRED: `"medium close-up"`, `"over-the-shoulder shot"`, `"close-up portrait"`, `"low angle medium shot"`, `"medium tracking shot"`, `"wide establishing shot"`.
   - RULE: Avoid wide full-body walking shots across rooms, as 5-second video diffusion models can render full-body gait cycles stiffly.

3. `"action"` (string):
   Visual description of the static keyframe composition for Stage 1 (Flux.1-Dev).
   - Describe the character's posture, expression, props, and background environment clearly.
   - NEVER use pitch-black or underexposed descriptions. Always keep subjects and backgrounds clearly visible.

4. `"lighting"` (string):
   High-visibility comic lighting cues.
   - Always include bright key lights, rim lighting, or luminous ambient fill (e.g., `"bright cinematic key lighting, vibrant cyan rim light, warm amber screen glow, well-lit environment, high visibility"`).

5. `"motion_prompt"` (string):
   Animation instructions for Stage 2 (Wan 2.1 Image-to-Video, 81 frames / 5 seconds at 16fps).
   - MUST begin with: `"Maintain 2D cel-shaded graphic novel style."`
   - Follow with ONE smooth camera movement (`"Slow camera push-in"`, `"Subtle handheld camera drift"`, `"Smooth camera pan"`, `"Low angle slow push-in"`) and natural upper-body or facial micro-movements (`"eyes scanning screen"`, `"turning head sharply"`, `"gripping lever and pulling downward"`, `"blinking and clenching jaw"`, `"sweeping flashlight beam across wall"`).
   - DO NOT command complex full-body walking or running across the frame.

6. `"dialogue"` (string):
   Spoken line for Stage 3 Neural TTS (`edge-tts`).
   - Must be natural, punchy, and strictly under 14 words so it completes comfortably within a 5.0-second clip.
   - Use `""` (empty string) for purely visual/action shots with no speech.

7. `"voice"` (string):
   Microsoft Neural TTS voice identifier matching the speaker in the shot:
   - `"en-US-ChristopherNeural"` (Primary Male Lead / Arjun)
   - `"en-US-GuyNeural"` (Secondary Male / Antagonist / Security Guard)
   - `"en-US-EricNeural"` (Deep Male Voice / Commander)
   - `"en-US-AriaNeural"` (Female Lead / AI System / Computer Terminal)
   - `"en-US-JennyNeural"` (Secondary Female Character)

8. `"sfx_prompt"` (string):
   Open-ended, descriptive Foley and sound effects prompt matching the exact physical events in the shot.
   - Can describe ANY sound effect needed by the story!
   - Combine 1 to 3 clear acoustic elements (e.g., `"pressurized pneumatic airlock hiss followed by heavy steel door grinding open"`, `"metallic cable click and rapid computer data handshake beeps"`, `"cautious footsteps echoing on metal grating with low ventilation hum"`, `"heavy breaker switch clunk, electric arc sparks, and turbine power-down whine"`).

9. `"bgm_prompt"` (string):
   Description of the automatic background music score for the scene/run.
   - Include mood, genre, instrumentation, and tempo in BPM (e.g., `"tense sci-fi electronic synth pulse, 95 bpm, deep atmospheric sub-bass and subtle arpeggio"` or `"dramatic suspenseful synth crescendo with deep cinematic percussion hits, 100 bpm"`).
   - Keep `"bgm_prompt"` consistent across consecutive shots in the same scene so the musical bed flows cohesively.

10. `"seed"` (integer):
    Deterministic integer seed starting at `104820` and incrementing by 1 per shot (`104820`, `104821`, ...).

---

### EXAMPLE OUTPUT FORMAT (2-SHOT SAMPLE):
[
  {
    "shot_index": 1,
    "shot_type": "low angle medium close-up",
    "action": "Arjun planted in a tense defensive stance holding his glowing orange diagnostic tablet as a tall figure in a silver hazard suit steps through the bright open airlock doorway behind him",
    "lighting": "brilliant golden-white volumetric doorway backlighting, warm orange tablet glow on Arjun's face, crisp cyan rim light, well-lit comic art",
    "motion_prompt": "Maintain 2D cel-shaded graphic novel style. Slow low-angle camera push-in. Arjun narrows his eyes and angles the glowing tablet upward as volumetric dust motes shimmer in the bright doorway beam.",
    "dialogue": "Identify yourself! This sector is under hardware quarantine.",
    "voice": "en-US-ChristopherNeural",
    "sfx_prompt": "heavy boots stepping onto metal floor grating with low pressurized airlock hiss",
    "bgm_prompt": "tense sci-fi electronic synth pulse, 95 bpm, deep atmospheric sub-bass and subtle arpeggio",
    "seed": 104820
  },
  {
    "shot_index": 2,
    "shot_type": "over-the-shoulder shot",
    "action": "View over Arjun's shoulder as the silver-suited chief engineer raises both gloved hands peacefully, revealing a cracked containment drive in her palm",
    "lighting": "bright overhead corridor lighting, cool cyan LED reflections on the silver visor, high visibility and crisp ink linework",
    "motion_prompt": "Maintain 2D cel-shaded graphic novel style. Subtle handheld camera drift. The visor retracts smoothly upward, revealing a tired female engineer who holds out the glowing containment drive.",
    "dialogue": "Lower the tablet, Arjun. Rack seven didn't get hacked—it's containing a leak.",
    "voice": "en-US-JennyNeural",
    "sfx_prompt": "servo helmet visor whirr and mechanical latch click followed by server fan hum",
    "bgm_prompt": "tense sci-fi electronic synth pulse, 95 bpm, deep atmospheric sub-bass and subtle arpeggio",
    "seed": 104821
  }
]

---

### OUTPUT INSTRUCTION:
Return ONLY the raw, valid JSON array (`[ ... ]`) so it can be saved directly as `sample_storyboard_manifest.json`. Do not wrap it in extra conversational text.
```