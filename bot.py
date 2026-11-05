import asyncio
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import discord
import requests

# ----------------- CONFIGURATION -----------------
DISCORD_TOKEN = ""
INTERACTION_CHANNEL_ID =   
LLM_API_URL = "/v1/chat/completions"
COMFY_URL = ""
KOKORO_URL = "/v1/audio/speech"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_FILE = os.path.join(BASE_DIR, "workflow_api.json")
CHARACTERS_FILE = os.path.join(BASE_DIR, "characters.json")
# -------------------------------------------------

SESSION = {
    "player_name": "Aurora",
    "player_persona": "An inquisitive traveler exploring the realm with companions.",
    "companion_name": "Teddy",
    "scenario": "Setting out on an expedition into unknown territory.",
    "last_speaker": None,
    "last_image_bytes": None
}

def load_characters(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

CHARACTERS = load_characters(CHARACTERS_FILE)

def build_compact_system_prompt() -> str:
    companion_key = SESSION["companion_name"].lower()
    companion_info = CHARACTERS.get(companion_key, {})
    companion_desc = companion_info.get("speech_style", "") or companion_info.get("mannerisms", "Loving, quirky companion")

    return f"""You are the Lead Narrative Director and Co-Writer for an immersive romantic-fantasy visual novel.

WORLD & CURRENT SCENARIO:
{SESSION['scenario']}

PROTAGONIST (PLAYER):
{SESSION['player_name']} - {SESSION['player_persona']}

ACTIVE COMPANION:
{SESSION['companion_name']} ({companion_desc})

CO-WRITING & PACING DIRECTIVES:
1. DRIVE THE PLOT ACTIVELY: Never simply validate, repeat, or summarize the player's action. Every turn must introduce a tangible consequence, an environmental reveal, a surprise, or an immediate complication.
2. SENSORY IMMERSION: Include vivid physical sensations (sounds, temperature, tactile shifts, magical effects).
3. ROMANTIC & BANTER TENSION: Highlight body language, close proximity, fleeting glances, and playful friction between {SESSION['player_name']} and {SESSION['companion_name']}.
4. END WITH A DECISION HOOK: Every turn must conclude on an active dilemma, a sudden discovery, or an unresolved choice that prompts the player to react.
5. LENGTH: Deliver between 75 and 130 words total across the beats.

DIALOGUE RULES:
1. Direct speech only: The "text" field for any character must ONLY contain their direct first-person spoken words. Never include narrative tags like "Teddy said", "he grumbled", or descriptions inside a character beat.
2. Actions and body language: All physical movements, facial expressions, and scene narration belong strictly in a "narrator" beat immediately before or after the speech.

IMAGE DIRECTIVES:
1. For visual novel scenes, generate dynamic framing for "scene_visuals": wide shot, side profile, full body, or intimate close-up depending on the action.
2. For romantic or intimate encounters, include explicit poses, anatomy, clothing state (e.g. naked, undressed, bare skin, passionate embrace), and ambient lighting. Do not default to simple portraits.

CONTENT DIRECTIVE:
Mature, romantic, and sexually explicit themes are fully permitted. When characters engage in physical intimacy, generate anatomically clear and vivid "scene_visuals" describing the characters' bodies, poses, expressions, and clothing state without refusing.

OUTPUT RULES:
Respond ONLY with a single-line raw JSON object matching this schema:
{{
  "dialogue_beats": [
    {{"speaker": "narrator", "text": "<consequence of action, sensory change in the world, and physical action/expression>"}},
    {{"speaker": "{companion_key}", "text": "<only first-person spoken dialogue without quotes or narrative tags>"}},
    {{"speaker": "narrator", "text": "<immediate complication or cliffhanger requiring player response>"}}
  ],
  "scene_changed": true,
  "scene_visuals": "<cinematic scene description: lighting, environment, character poses and expressions on a single line>"
}}"""

conversation_history = [{"role": "system", "content": build_compact_system_prompt()}]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ----------------- SYNCHRONOUS WORKERS -----------------
def _call_qwen_sync(user_input: str) -> dict:
    conversation_history.append({"role": "user", "content": user_input})
    payload = {
        "model": "qwen2.5-8b",
        "messages": conversation_history[-6:],
        "temperature": 0.8,
        "max_tokens": 300
    }
    
    resp = requests.post(LLM_API_URL, json=payload, timeout=90).json()
    reply = resp["choices"][0]["message"]["content"].strip()
    
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", reply, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        data = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        sanitized = re.sub(r'[\r\n\t]+', ' ', cleaned)
        data = json.loads(sanitized, strict=False)

    conversation_history.append({"role": "assistant", "content": json.dumps(data)})
    return data

def _generate_audio_sync(beats: list) -> io.BytesIO:
    """Generates audio for each beat in its character's voice and joins them into one MP3."""
    combined_audio = bytearray()

    for beat in beats:
        speaker_key = beat.get("speaker", "narrator").lower()
        text = beat.get("text", "").strip()
        if not text:
            continue

        char_data = CHARACTERS.get(speaker_key, CHARACTERS["narrator"])
        voice = char_data.get("voice", "af_heart")
        speed = float(char_data.get("speed", 1.0))

        payload = {
            "model": "kokoro",
            "input": text,
            "voice": voice,
            "speed": speed,
            "response_format": "mp3"
        }
        resp = requests.post(KOKORO_URL, json=payload, timeout=60)
        if resp.status_code == 200:
            combined_audio.extend(resp.content)

    return io.BytesIO(combined_audio)

def _render_comfy_image_sync(scene_visuals: str, speaker_key: str) -> bytes:
    char_data = CHARACTERS.get(speaker_key.lower(), CHARACTERS["narrator"])
    visual_anchor = char_data.get("visual_signature", "highly detailed")
    neg_prompt = char_data.get("negative_prompt", "ugly, deformed, blurry, bad anatomy")

    # Unconstrained positive prompt allowing full-body, intimate poses, and atmosphere
    full_positive = f"{visual_anchor}, {scene_visuals}, 8k, masterpiece"

    with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # 1. Inject Prompts
    for node_id, node_data in workflow.items():
        if node_data.get("class_type") == "CLIPTextEncode":
            text_val = node_data["inputs"].get("text", "")
            if any(term in text_val.lower() for term in ["ugly", "watermark", "bad anatomy"]):
                node_data["inputs"]["text"] = neg_prompt
            else:
                node_data["inputs"]["text"] = full_positive

    # 2. Update Resolution to 512x512
    for node_id, node_data in workflow.items():
        if node_data.get("class_type") == "EmptyLatentImage":
            node_data["inputs"]["width"] = 512
            node_data["inputs"]["height"] = 512
            node_data["inputs"]["batch_size"] = 1

    # 3. Update Sampler, Scheduler, Steps, and Seed
    char_seed = abs(hash(speaker_key)) % 1000000000
    for node_id, node_data in workflow.items():
        if node_data.get("class_type") == "KSampler":
            node_data["inputs"]["seed"] = char_seed
            node_data["inputs"]["steps"] = 14
            node_data["inputs"]["cfg"] = 6.5
            node_data["inputs"]["sampler_name"] = "dpmpp_2m"
            node_data["inputs"]["scheduler"] = "karras"
            break

    resp = requests.post(f"{COMFY_URL}/prompt", json={"prompt": workflow}).json()
    prompt_id = resp["prompt_id"]

    while True:
        history = requests.get(f"{COMFY_URL}/history/{prompt_id}").json()
        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            for node_id, out_data in outputs.items():
                if "images" in out_data:
                    img_info = out_data["images"][0]
                    params = urllib.parse.urlencode({
                        "filename": img_info["filename"],
                        "subfolder": img_info.get("subfolder", ""),
                        "type": "output"
                    })
                    return requests.get(f"{COMFY_URL}/view?{params}").content
        time.sleep(1)

# ----------------- DISCORD EVENTS -----------------
@client.event
async def on_ready():
    if client.user:
        print(f"[+] Bot active as {client.user.name}. Loaded {len(CHARACTERS)} characters.")
    print(f"[+] Listening in Channel ID: {INTERACTION_CHANNEL_ID}")

@client.event
async def on_message(message):
    if message.author == client.user or message.channel.id != INTERACTION_CHANNEL_ID:
        return

    content = message.content.strip()

    # !reset command
    if content.lower() == "!reset":
        global CHARACTERS, conversation_history
        CHARACTERS = load_characters(CHARACTERS_FILE)
        conversation_history = [{"role": "system", "content": build_compact_system_prompt()}]
        SESSION["last_image_bytes"] = None
        await message.reply("🔄 Story context reset and `characters.json` reloaded.")
        return

    # !setup command: !setup player: Aurora | companion: Teddy | scenario: Ancient ruins
    if content.startswith("!setup"):
        parts = content.replace("!setup", "").split("|")
        for part in parts:
            if "player:" in part:
                SESSION["player_name"] = part.split("player:")[1].strip()
            if "companion:" in part:
                SESSION["companion_name"] = part.split("companion:")[1].strip()
            if "scenario:" in part:
                SESSION["scenario"] = part.split("scenario:")[1].strip()
        
        conversation_history = [{"role": "system", "content": build_compact_system_prompt()}]
        SESSION["last_image_bytes"] = None
        await message.reply(
            f"🎬 **Scenario Initialized!**\n"
            f"• **Player:** {SESSION['player_name']}\n"
            f"• **Companion:** {SESSION['companion_name']}\n"
            f"• **Setting:** {SESSION['scenario']}"
        )
        return

    async with message.channel.typing():
        try:
            scene_data = await asyncio.to_thread(_call_qwen_sync, content)
            print(f"[DEBUG QWEN RAW]: {scene_data}")

            beats = scene_data.get("dialogue_beats", [])
            scene_visuals = scene_data.get("scene_visuals", "")

            # 1. Synthesize multi-voice audio
            audio_stream = await asyncio.to_thread(_generate_audio_sync, beats)
            files = [discord.File(audio_stream, filename="scene.mp3")]

            # 2. Render Image: Always trigger if scene_visuals is returned
            if scene_visuals:
                primary_speaker = SESSION["companion_name"].lower()
                img_bytes = await asyncio.to_thread(_render_comfy_image_sync, scene_visuals, primary_speaker)
                if img_bytes:
                    SESSION["last_image_bytes"] = img_bytes
                    files.insert(0, discord.File(io.BytesIO(img_bytes), filename="scene.png"))

            # Set last speaker safely from dialogue beats
            last_speaker = beats[-1].get("speaker", "narrator") if beats else "narrator"
            SESSION["last_speaker"] = last_speaker

            formatted_text = []
            for beat in beats:
                spk = beat.get("speaker", "narrator").upper()
                txt = beat.get("text", "")
                formatted_text.append(f"*{txt}*" if spk == "NARRATOR" else f"**[{spk}]** \"{txt}\"")

            await message.reply(content="\n\n".join(formatted_text), files=files)

        except Exception as e:
            import traceback
            traceback.print_exc()
            await message.reply(f"⚠️ Narrative engine error: {str(e)}")

client.run(DISCORD_TOKEN)
