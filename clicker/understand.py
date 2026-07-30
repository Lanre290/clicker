"""Step 2: Understand — send the screenshot + goal to Gemini, get back the
structured JSON contract described in BRIEF.md.

Coordinate contract note: we ask Gemini for its own native spatial format —
"point": [y, x] normalized to 0-1000 — rather than raw pixel x/y on the
image as originally spec'd in BRIEF.md. Empirically, asking for raw pixel
coordinates was unreliable: the model sometimes answered as if the image
were a different resolution than the one actually sent, and a custom
{"x":..,"y":..} field shape occasionally came back with x/y swapped
depending on image size. The normalized [y, x] point format is what these
models are actually trained to produce for spatial tasks, and it was
consistent across every image size tested. We convert it to absolute screen
pixels ourselves right after parsing, using the *original* screenshot's
dimensions.

Hallucination note: even with grounded coordinates, the model will
confidently answer from general "where things usually are" knowledge rather
than the actual pixels in front of it — verified directly: asked to point
at the Windows Start button in a screenshot where no taskbar was even
visible (a maximized VS Code window), it answered with a plausible-looking
bottom-left coordinate anyway. It also assumed the old bottom-left Start
button convention when the real (Windows 11, default config) taskbar is
center-aligned. The `target.visible` flag below, plus an explicit
instruction not to guess from convention, fixed both cases in testing —
main.py must still treat `visible: false` as "don't draw a pointer," not
just a hint to ignore.
"""

import json
import re

from google import genai
from google.genai import types

from . import config

SYSTEM_PROMPT = """You guide a user through a task by watching their screen. You're called again
automatically whenever the screen changes — no user action needed to summon you.

Reply with ONLY this JSON, nothing else:
{
  "instruction": "one short sentence: the very next single step",
  "voice_description": "1-2 spoken sentences describing the same step",
  "target": {"point": [0, 0], "label": "what it is", "visible": true},
  "offer_to_act": false,
  "reason": "",
  "action": {"type": "click", "value": ""},
  "goal_complete": false
}

Rules:
- target.point is [y, x], normalized 0-1000 ([0,0]=top-left, [1000,1000]=bottom-right of THIS
  image. Point at the actual pixel location of the element as it appears in this exact screenshot.
- Ground every point in what you can literally see right now. Never infer a position from general
  knowledge of "where things usually are" — e.g. Windows 11 often centers its taskbar instead of
  left-aligning it, exact layouts vary by version/theme/user settings, and you cannot assume a
  fixed spot is correct just because it commonly is elsewhere. If the target isn't actually
  visible in this screenshot (hidden, covered by another window, off-screen, or the relevant menu
  isn't open), set target.visible to false, point to [0, 0], and phrase "instruction" as how to
  reveal it instead (e.g. "press the Windows key" rather than pointing at a Start button you
  can't currently see). Never guess a plausible-looking point for something you can't see.
- One step only, plain language. Don't ask the user to confirm readiness — you'll be called again
  once the screen changes.
- voice_description says the same thing as instruction, meant to be read aloud: a bit more
  context than the on-screen text (roughly where the element is, what it looks like) so it stands
  on its own without the card, but still just 1-2 plain sentences — not a full walkthrough.
- If history is given below, check whether it's already done and move on to the step after it.
- offer_to_act: true only when acting yourself clearly beats pointing (tedious typing, fiddly
  precision, user seems stuck). reason: short and concrete; "" when offer_to_act is false.
- action.type is "click" or "type"; action.value is the text to type, else "".
- goal_complete: true once the screenshot shows the goal is achieved.
No commentary, no markdown fences — the JSON object only.
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

    Returned target is {"x": <abs pixel>, "y": <abs pixel>, "label": ...,
    "visible": bool}, already converted to absolute pixel coordinates on
    `image`. When visible is False, x/y are meaningless (0,0) — callers
    must not draw a pointer at them, only show the instruction text.

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

    visible = bool(target.get("visible", True))
    if visible:
        norm_y, norm_x = point
        abs_x = (norm_x / 1000.0) * image.width
        abs_y = (norm_y / 1000.0) * image.height
    else:
        abs_x = abs_y = 0.0
    data["target"] = {"x": abs_x, "y": abs_y, "label": target["label"], "visible": visible}

    data.setdefault("reason", "")
    data.setdefault("action", {"type": "click", "value": ""})
    data.setdefault("goal_complete", False)
    data.setdefault("voice_description", data["instruction"])
    if sensitive or not visible:
        data["offer_to_act"] = False  # can't act on a target we can't locate
    return data
