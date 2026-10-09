from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("plugin.hermes-vision-inline")
__version__ = "1.1.0"
_TOOL = "vision_analyze"
_VALID_MODES = frozenset({"auto", "native", "text"})
_SEES_CACHE: Dict[Tuple[str, str], bool] = {}


def _normalise_id(model: str) -> str:
    m = (model or "").strip().lower()
    m = m.rsplit("/", 1)[-1]
    m = m.split(":", 1)[0]
    m = m.replace(".", "-")
    return re.sub(r"-\d{4,8}$", "", m)


def _catalog_data() -> Dict[str, Any]:
    try:
        from agent.models_dev import fetch_models_dev
        return fetch_models_dev(allow_network=False) or {}
    except Exception as exc:
        logger.debug("hermes-vision-inline: catalog unavailable: %s", exc)
        return {}


def _entry_for(provider: str, model: str) -> Optional[bool]:
    if not model:
        return None
    try:
        from agent.models_dev import get_model_capabilities
        caps = get_model_capabilities(provider, model, allow_network=False)
        if caps is not None:
            return bool(caps.supports_vision)
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
                return True
            saw_text_only = True
    return False if saw_text_only else None


def _sees_images(provider: str, model: str) -> bool:
    key = (provider, model)
    if key not in _SEES_CACHE:
        verdict = bool(_entry_for(provider, model))
        _SEES_CACHE[key] = verdict
        logger.info("hermes-vision-inline: catalog says %s takes images -> %s", model, verdict)
    return _SEES_CACHE[key]


def _identity() -> Tuple[str, str]:
    try:
        from agent.auxiliary_client import _read_main_model, _read_main_provider
        return _read_main_provider() or "", _read_main_model() or ""
    except Exception as exc:
        logger.debug("hermes-vision-inline: model id unavailable: %s", exc)
        return "", ""


def _config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly()
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        logger.debug("hermes-vision-inline: config unavailable: %s", exc)
        return {}


def _image_input_mode(cfg: Dict[str, Any]) -> str:
    agent = cfg.get("agent")
    if not isinstance(agent, dict):
        return "auto"
    mode = agent.get("image_input_mode")
    mode = mode.strip().lower() if isinstance(mode, str) else ""
    return mode if mode in _VALID_MODES else "auto"


def _accepts_images_in_tool_results(provider: str, model: str, cfg: Dict[str, Any]) -> bool:
    try:
        from tools.vision_tools import _accepts_tool_result_images
        return bool(_accepts_tool_result_images(provider, model, cfg))
    except Exception as exc:
        logger.debug("hermes-vision-inline: tool-result image gate unavailable: %s", exc)
        return False


def _route(provider: str, model: str, cfg: Dict[str, Any]) -> str:
    if _image_input_mode(cfg) == "text":
        return "aux"
    if not _accepts_images_in_tool_results(provider, model, cfg):
        return "aux"
    return "native" if _sees_images(provider, model) else "aux"


async def _vision_inline(args: Dict[str, Any], task_id: Optional[str]) -> Any:
    from tools.vision_tools import _vision_analyze_native
    logger.info("hermes-vision-inline: attaching image inline")
    return await _vision_analyze_native(
        args.get("image_url", ""), args.get("question", ""),
        task_id=task_id, region=args.get("region"))


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
    if tool_name != _TOOL:
        return next_call(args)
    if _route(*_identity(), _config()) != "native":
        return next_call(args)
    try:
        return _run_sync(_vision_inline(args, kw.get("task_id")))
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
