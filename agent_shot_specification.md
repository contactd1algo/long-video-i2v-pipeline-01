# Agent Prompt Specification: Storyboard Manifest (`sample_storyboard_manifest.json`)

Use this specification when instructing any LLM or AI agent to generate the JSON storyboard manifest (`sample_storyboard_manifest.json`) for the **Comic-to-Video Production Pipeline**.

---

## 1. File Structure & Format
The file must be a **valid JSON array** containing ordered shot objects (`shot_index` from `1` to `N`). Each shot represents a **5-second video clip** (81 frames at 16 fps).

```json
[
  {
    "shot_index": 1,
    "shot_type": "wide establishing shot",
    "action": "Description of the visual action taking place",
    "lighting": "Detailed lighting cues for high visibility and vibrant cel-shading",
    "motion_prompt": "Cinematic camera movement and character motion instructions for Wan 2.1 I2V",
    "dialogue": "Spoken line of character dialogue (optional, leave blank for pure action)",
    "voice": "Neural TTS voice ID (e.g., en-US-ChristopherNeural)",
    "sfx_type": "Procedural Foley sound effect category",
    "seed": 104820
  }
]
```

---

## 2. Field Definitions & Guidelines

1. **`shot_index`** (`integer`): Sequential number starting at `1`.
2. **`shot_type`** (`string`): Framing designation (e.g., `"wide establishing shot"`, `"medium close-up"`, `"over-the-shoulder shot"`, `"low angle action shot"`).
3. **`action`** (`string`): **Crucial for Image Generation (Stage 1).** Describes what is happening inside the static panel. 
   * *Rule:* Avoid dark, muddy, or underexposed phrasing. Use bright, high-clarity terms so Flux.1-Dev renders crisp cel-shaded plates.
4. **`lighting`** (`string`): **Crucial for Color Grading.** Specifies key lights, rim lights, and atmospheric highlights (e.g., `"bright cinematic key lighting, vibrant cyan rim light, well-lit environment"`).
5. **`motion_prompt`** (`string`): **Crucial for Video Generation (Stage 2).** Instructs the Wan 2.1 I2V model on how to animate the static panel.
   * *Rule:* To prevent robotic motion (such as unnatural walking loops), focus on **subtle camera push-ins, pans, blinks, micro-expressions, glowing pulsing effects, and slight weight shifts** rather than complex full-body locomotion.
6. **`dialogue`** (`string`): The exact spoken line for Stage 3 Neural TTS. Keep it concise (under 15 words per 5-second shot).
7. **`voice`** (`string`): Edge-TTS voice identifier. 
   * Male character: `"en-US-ChristopherNeural"` or `"en-US-SteffanNeural"`
   * AI / System / Female character: `"en-US-AriaNeural"` or `"en-US-JennyNeural"`
8. **`sfx_type`** (`string`): Procedural audio category. Supported types:
   * `"server_hum"` (ambient industrial drone)
   * `"alarm"` (pulsing klaxon siren)
   * `"power_down"` (descending frequency sweep)
   * `"door_creak"` (heavy metallic resonance)
   * `"beeps"` (terminal datalink pulses)
9. **`seed`** (`integer`): Unique random seed (e.g., `104820`, `104821`, ...) for deterministic reproducibility.

---

## 3. Example Prompt to Feed Your AI Agent
> "Act as a professional storyboard artist and scriptwriter for a 2D sci-fi comic animation. Generate a 10-shot JSON manifest (`sample_storyboard_manifest.json`) following the exact schema provided in the specification. Ensure action prompts emphasize micro-movements and camera framing rather than complex walking sequences to prevent motion artifacts, and include engaging dialogue and SFX tags for each shot."
