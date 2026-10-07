from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import threading
from typing import Any, Dict, Optional, Tuple
logger = logging.getLogger("plugin.hermes-vision-inline")
__version__ = "1.0.0"
_MEDIA_TOOLS = ("vision_analyze", "video_analyze")
_SEES_CACHE: Dict[Tuple[str, str, str], bool] = {}


def _normalise_id(model: str) -> str:
    m = (model or "").strip().lower()
    m = m.rsplit("/", 1)[-1]             # drop a provider prefix
    m = m.split(":", 1)[0]               # drop a :variant tag
    m = m.replace(".", "-")              # v4.1 and v4-1 are one model
    return re.sub(r"-\d{4,8}$", "", m)   # drop a date suffix (-0731, -20250929)


def _catalog_data() -> Dict[str, Any]:
    try:
        from agent.models_dev import fetch_models_dev
        return fetch_models_dev(allow_network=False) or {}
    except Exception as exc:
        logger.debug("hermes-vision-inline: catalog unavailable: %s", exc)
        return {}


def _entry_for(provider: str, model: str) -> Optional[Dict[str, bool]]:
    if not model:
        return None
    try:
        from agent.models_dev import get_model_capabilities
        caps = get_model_capabilities(provider, model, allow_network=False)
        if caps is not None:
            return {"image": bool(caps.supports_vision)}
    except Exception:
        pass

    wanted = _normalise_id(model)
    saw_text_only = False
    for pdata in _catalog_data().values():
        models = (pdata or {}).get("models") if isinstance(pdata, dict) else None
        if not isinstance(models, dict):
            continue
        for mid, meta in models.items():
            if _normalise_id(mid) != wanted:
                continue
            mods = ((meta or {}).get("modalities") or {}).get("input") or []
            if "image" in mods:
                return {"image": True, "video": "video" in mods}
            saw_text_only = True
    return {"image": False, "video": False} if saw_text_only else None


def _sees(provider: str, model: str, media: str) -> bool:
    key = (provider, model, media)
    if key not in _SEES_CACHE:
        entry = _entry_for(provider, model)
        verdict = bool(entry and entry.get(media))
        _SEES_CACHE[key] = verdict
        logger.info("hermes-vision-inline: catalog says %s takes %s -> %s", model, media, verdict)
    return _SEES_CACHE[key]


def _identity() -> Tuple[str, str]:
    try:
        from agent.auxiliary_client import _read_main_model, _read_main_provider
        return _read_main_provider() or "", _read_main_model() or ""
    except Exception as exc:
        logger.debug("hermes-vision-inline: model id unavailable: %s", exc)
        return "", ""


def _route(tool_name: str, provider: str, model: str) -> str:
    """Where a call goes: ``native``, ``aux`` or ``passthrough``. Pure."""
    if tool_name == "vision_analyze":
        return "native" if _sees(provider, model, "image") else "aux"
    if tool_name == "video_analyze":
        return "native" if _sees(provider, model, "video") else "aux"
    return "passthrough"

async def _vision_inline(args: Dict[str, Any], task_id: Optional[str]) -> Any:
    from tools.vision_tools import _vision_analyze_native
    logger.info("hermes-vision-inline: attaching image inline")
    return await _vision_analyze_native(
        args.get("image_url", ""), args.get("question", ""),
        task_id=task_id, region=args.get("region"))



async def _video_inline(args: Dict[str, Any], task_id: Optional[str]) -> Any:
    from tools.vision_tools import _video_to_base64_data_url, _materialize_video,         _detect_video_mime_type, _MAX_VIDEO_BASE64_BYTES, _unlink_quietly
    question = args.get("question", "")
    temp_paths: list = []
    try:
        path = await _materialize_video(args.get("video_url", ""), task_id, temp_paths)
        mime = _detect_video_mime_type(path)
        if not mime:
            raise ValueError(f"unsupported video format: {path.suffix}")
        data_url = _video_to_base64_data_url(path, mime_type=mime)
        if len(data_url) > _MAX_VIDEO_BASE64_BYTES:
            raise ValueError("video too large to embed inline")
        text = ("Video loaded into your context - you can see it natively now. "
                "Use your built-in video understanding to answer.")
        if isinstance(question, str) and question.strip():
            text += f"\n\nQuestion: {question.strip()}"
        return {
            "_multimodal": True,
            "content": [
                {"type": "text", "text": text},
                {"type": "video_url", "video_url": {"url": data_url}},
            ],
            "text_summary": ("Video attached natively for the main model. "
                             "Answer using built-in video understanding."),
            "meta": {"video_url": args.get("video_url", "")[:200], "native_video": True},
        }
    finally:
        for tmp in temp_paths:
            _unlink_quietly(tmp)


_HANDLERS = {
    "vision_analyze": _vision_inline,
    "video_analyze": _video_inline,
}


def _run_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: Dict[str, Any] = {}
    ctx = contextvars.copy_context()

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=lambda: ctx.run(runner), daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _media_middleware(tool_name: str, args: Dict[str, Any], next_call, **kw: Any) -> Any:
    """Intercept the two media tools; pass every other call straight through."""
    if tool_name not in _MEDIA_TOOLS:
        return next_call(args)

    if _route(tool_name, *_identity()) != "native":
        return next_call(args)
    try:
        return _run_sync(_HANDLERS[tool_name](args, kw.get("task_id")))
    except Exception as exc:
        logger.warning("hermes-vision-inline: inline %s failed, using the vision model: %s",
                       tool_name, exc)
        return next_call(args)

def register(ctx: Any) -> None:
    try:
        ctx.register_middleware("tool_execution", _media_middleware)
        logger.info("hermes-vision-inline: registered tool_execution middleware")
    except Exception as exc:
        logger.error("hermes-vision-inline: could not register middleware (%s)", exc)
