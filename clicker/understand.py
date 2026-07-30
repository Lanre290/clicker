"""Step 2: Understand — send the screenshot + goal to Gemini, get back the
structured JSON contract described in BRIEF.md.

Coordinate contract note: we ask Gemini for its own native spatial format —
"point": [y, x] normalized to 0-1000 — rather than raw pixel x/y on the
image as originally spec'd in BRIEF.md. Empirically (see PROJECT NOTES /
commit history), asking for raw pixel coordinates was unreliable: the model
sometimes answered as if the image were a different resolution than the one
actually sent, and a custom {"x":..,"y":..} field shape occasionally came
back with x/y swapped depending on image size. The normalized [y, x] point
format is what these models are actually trained to produce for spatial
tasks, and it was consistent across every image size tested (1920x1080 down
to 800x450) including an asymmetric real-UI target (the taskbar Start
button). We convert it to absolute screen pixels ourselves right after
parsing, using the *original* screenshot's dimensions — so this is also
independent of whatever size we actually uploaded.
"""

import json
import re

from google import genai
from google.genai import types

from . import config

SYSTEM_PROMPT = """You are a screen-guide assistant helping a user complete a goal on their computer.
You are watching their screen continuously: you'll be called again automatically each time the
screen changes meaningfully, with no user action needed to summon you. Each call shows you the
current screenshot and the user's goal.

Respond with ONLY a single JSON object, no other text, no markdown fences, matching this exact shape:
{
  "instruction": "short text telling the user what to do next",
  "target": {"point": [0, 0], "label": "what the arrow points at"},
  "offer_to_act": false,
  "reason": "why acting would help (only when offer_to_act is true, else empty string)",
  "action": {"type": "click", "value": ""},
  "goal_complete": false
}

Rules:
- "target.point" is [y, x], normalized to a 0-1000 scale where [0, 0] is the top-left corner of
  the image and [1000, 1000] is the bottom-right corner — NOT raw pixels, and independent of the
  image's actual resolution. Point at the center of the UI element the user should interact with
  next.
- "instruction" is one short sentence, plain language, describing the very next single step
  toward the goal. Never describe more than one step.
- You will be called again automatically once the screen changes, so do not ask the user to
  confirm they're ready — just state the next step for what's on screen right now.
- Judge from the screenshot whether the previous instruction (see history below, if given) was
  already carried out, and move on to the step after it. Don't repeat a step that's visibly done.
- Set "offer_to_act" to true only when performing this one step yourself would clearly help
  more than pointing at it (e.g. tedious typing, a fiddly precise action, or the user seems
  stuck). Otherwise leave it false and just guide.
- "reason" is a short, concrete, honest explanation of why acting would help. Leave it ""
  when offer_to_act is false.
- "action.type" is "click" or "type". "action.value" is the text to type when type, else "".
- Set "goal_complete" to true once the screenshot shows the goal has been achieved, and point
  "target" at whatever confirms it. Leave it false otherwise.
Return nothing but the JSON object — no commentary, no markdown fences.
"""


class UnderstandError(Exception):
    pass


def _extract_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    return json.loads(text)


_client = None


def _get_client():
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise UnderstandError(
                "GEMINI_API_KEY environment variable is not set. "
                "Set it before running: setx GEMINI_API_KEY \"your-key\" (new shell needed) "
                "or $env:GEMINI_API_KEY=\"your-key\" for the current session."
            )
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def understand(image, goal, history=None, window_title=None, sensitive=False):
    """Call Gemini with the screenshot + goal, return the parsed contract dict.

    Returned target is {"x": <abs pixel>, "y": <abs pixel>, "label": ...},
    already converted to absolute pixel coordinates on `image` — callers
    don't need to know about the normalized wire format above.

    window_title: title of the currently focused window, if known — gives
    Gemini context about which app the user is actually in.
    sensitive: if True, the focused window matched the sensitive-app guard;
    Gemini is told to guide only. This is a hint, not the enforcement point —
    main.py still forces offer_to_act off regardless of what comes back.
    """
    client = _get_client()

    # Downscale before upload: Gemini tiles large images into more patches
    # (more tokens, more latency) than the coordinate accuracy needs, and it
    # doesn't affect accuracy since coordinates are normalized (see module
    # docstring) — we scale back using the ORIGINAL image's dimensions below.
    sent_image = image
    if max(image.size) > config.IMAGE_MAX_DIMENSION:
        new_size = (
            config.IMAGE_MAX_DIMENSION,
            round(image.size[1] * config.IMAGE_MAX_DIMENSION / image.size[0]),
        )
        sent_image = image.resize(new_size)

    prompt_parts = [SYSTEM_PROMPT, f"User's goal: {goal}"]
    if window_title:
        prompt_parts.append(f"Currently focused window: {window_title!r}")
    if sensitive:
        prompt_parts.append(
            "This window looks sensitive (password manager / financial app). "
            "Guide only — set offer_to_act to false no matter what."
        )
    if history:
        prompt_parts.append(
            "Instructions already given earlier this session (don't repeat a completed one): "
            + " | ".join(history)
        )
    prompt_parts.append(sent_image)

    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt_parts,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level=config.THINKING_LEVEL)
            ),
        )
    except Exception as e:  # network/auth/model errors all surface here
        raise UnderstandError(f"Gemini API call failed: {e}") from e

    try:
        data = _extract_json(response.text)
    except (json.JSONDecodeError, AttributeError, ValueError) as e:
        raw = getattr(response, "text", response)
        raise UnderstandError(
            f"Could not parse Gemini response as JSON: {e}\nRaw response: {raw}"
        ) from e

    for key in ("instruction", "target", "offer_to_act"):
        if key not in data:
            raise UnderstandError(f"Gemini response missing required key '{key}': {data}")
    target = data["target"]
    if "point" not in target or "label" not in target:
        raise UnderstandError(f"Gemini response target missing 'point'/'label': {data}")
    point = target["point"]
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise UnderstandError(f"Gemini response target.point malformed: {data}")

    norm_y, norm_x = point
    abs_x = (norm_x / 1000.0) * image.width
    abs_y = (norm_y / 1000.0) * image.height
    data["target"] = {"x": abs_x, "y": abs_y, "label": target["label"]}

    data.setdefault("reason", "")
    data.setdefault("action", {"type": "click", "value": ""})
    data.setdefault("goal_complete", False)
    if sensitive:
        data["offer_to_act"] = False  # enforced regardless of what Gemini returned
    return data
