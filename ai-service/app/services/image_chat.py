import base64
import io
import json
import logging
import re

import httpx

from app.core.config import settings
from app.schemas.image import ImageAnalyzeResponse, ImageInfo
from app.services import catalog, vision
from app.services.chatbot.service import _clean_answer, _ensure_product_cta, _keep_relevant_ids

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

MAX_SIDE = 1024
MAX_QUERIES = 6

EXTRACT_PROMPT = (
    "Bạn là bộ phận phân tích ảnh của siêu thị Muadee. Hãy xem ảnh và trả về DUY NHẤT một JSON hợp lệ, "
    "không thêm chữ ngoài JSON, theo cấu trúc:\n"
    '{"images":[{"description":"mô tả ngắn ảnh bằng tiếng Việt","ocrText":"toàn bộ chữ đọc được, nếu không có để chuỗi rỗng",'
    '"items":["tên nguyên liệu/sản phẩm/món ăn nhận diện được"]}],'
    '"summary":"tóm tắt nhu cầu của khách dựa trên ảnh và câu hỏi"}\n'
    "Quy tắc: mô tả bằng tiếng Việt; items chỉ gồm tên ngắn gọn (ví dụ: cà chua, cá hồi, sữa tươi, nồi lẩu); "
    "không bịa sản phẩm không thấy trong ảnh; nếu ảnh có chữ/sản phẩm hãy đọc cả chữ. "
    "Nếu có nhiều ảnh, mô tả từng ảnh theo đúng thứ tự."
)

COMPOSE_SYSTEM = (
    "Bạn là trợ lý mua sắm của siêu thị Muadee, tư vấn viên tận tâm. Trả lời bằng tiếng Việt tự nhiên, "
    "xưng 'mình' và gọi khách là 'bạn'. Chỉ dùng dữ liệu sản phẩm được cung cấp, không bịa sản phẩm/giá. "
    "Trình bày có tiêu đề viết hoa và gạch đầu dòng. Với món ăn, hướng dẫn từng bước, thời gian và mẹo. "
    "Cuối câu trả lời mời khách bấm 'Xem chi tiết' hoặc 'Thêm vào giỏ' ở thẻ sản phẩm bên dưới và hỏi một câu dẫn dắt. "
    "Không quá 300 từ."
)


def analyze(images: list[bytes], message: str, store_id: str | None) -> ImageAnalyzeResponse:
    provider = settings.llm_provider.strip().lower()
    has_key = bool((settings.llm_api_key or settings.anthropic_api_key).strip())
    if provider in {"openai", "anthropic", "claude"} and has_key:
        try:
            return _analyze_with_claude(images, message, store_id)
        except Exception as exc:
            log.warning("Claude vision failed, fallback legacy: %s", exc)
    try:
        return _analyze_legacy(images, message, store_id)
    except Exception:
        log.exception("Image analyze failed")
        return ImageAnalyzeResponse(
            answer="Mình chưa phân tích được ảnh này. Bạn thử gửi lại ảnh rõ hơn (JPG/PNG/WebP, dưới 5 MB) hoặc mô tả giúp mình nhé.",
            images=[],
            productIds=[],
        )


def _analyze_with_claude(images: list[bytes], message: str, store_id: str | None) -> ImageAnalyzeResponse:
    prepared = [_prepare_image(data) for data in images]
    extraction = _extract(prepared, message)
    infos, queries = _extract_info(extraction, message)
    log.info("vision images=%d items=%s", len(images), queries)

    rows: list[dict] = []
    seen: set[str] = set()
    for query in queries[:MAX_QUERIES]:
        for row in catalog.search(query, store_id, 3):
            if row["productId"] not in seen:
                seen.add(row["productId"])
                rows.append(row)
    product_ids = [row["productId"] for row in rows[:8]]
    names = {row["productId"]: row["name"] for row in rows}
    listing = "\n".join(
        f"- {row['name']}: {_money(row['price'])}/{row.get('unit') or 'sản phẩm'}"
        f" ({row.get('categoryName') or 'danh mục khác'})"
        for row in rows
    )
    log.info("vision matched products=%d", len(product_ids))

    answer = _compose(infos, message, listing)
    if not answer:
        answer = _fallback_answer(infos, listing)
    kept = _keep_relevant_ids(answer, product_ids, names)
    answer = _ensure_product_cta(_clean_answer(answer), kept, names)
    return ImageAnalyzeResponse(answer=answer, images=infos, productIds=kept)


def _prepare_image(data: bytes) -> str:
    """Resize/compress để payload gửi Claude nhẹ và ổn định."""
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract(prepared: list[str], message: str) -> dict:
    text_prompt = f"{EXTRACT_PROMPT}\n\nCâu hỏi của khách: {message or '(không có)'}"
    if _is_anthropic():
        content: list[dict] = [{"type": "text", "text": text_prompt}]
        for b64 in prepared:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
        data = _post_anthropic(
            system=None,
            messages=[{"role": "user", "content": content}],
            max_tokens=800,
            temperature=0.1,
        )
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    else:
        content = [{"type": "text", "text": text_prompt}]
        for b64 in prepared:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        data = _post_openai(
            messages=[{"role": "user", "content": content}],
            max_tokens=800,
            temperature=0.1,
        )
        text = data["choices"][0]["message"].get("content") or ""
    return _parse_json(text)


def _extract_info(extraction: dict, message: str) -> tuple[list[ImageInfo], list[str]]:
    infos: list[ImageInfo] = []
    queries: list[str] = []
    raw_images = extraction.get("images") if isinstance(extraction, dict) else None
    if isinstance(raw_images, list):
        for item in raw_images:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or "").strip()
            ocr = str(item.get("ocrText") or "").strip()
            items = [str(name).strip() for name in (item.get("items") or []) if str(name).strip()]
            for name in items:
                if name not in queries:
                    queries.append(name)
            infos.append(ImageInfo(description=description, ocrText=ocr, matches=[]))
    if not infos:
        summary = str(extraction.get("summary") or "").strip() if isinstance(extraction, dict) else ""
        infos.append(ImageInfo(description=summary, ocrText="", matches=[]))
    if not queries:
        if message.strip():
            queries.append(message.strip())
        for info in infos:
            if info.ocrText:
                queries.append(info.ocrText[:120])
    return infos, queries


def _compose(infos: list[ImageInfo], message: str, listing: str) -> str:
    parts = []
    for index, info in enumerate(infos, 1):
        parts.append(
            f"Ảnh {index}: {info.description or 'không mô tả được'}; "
            f"chữ đọc được: {info.ocrText or 'không có'}"
        )
    user_prompt = (
        f"Khách gửi {len(infos)} ảnh và hỏi: {message or 'Gợi ý sản phẩm phù hợp với ảnh'}\n"
        + "\n".join(parts)
        + f"\n\nSản phẩm thật đang bán tại Muadee:\n{listing or '(không tìm thấy sản phẩm phù hợp)'}\n\n"
        "Hãy: (1) xác nhận ngắn gọn mình thấy gì trong ảnh; (2) gợi ý sản phẩm phù hợp kèm giá/đơn vị; "
        "(3) hướng dẫn cách dùng/nấu nếu phù hợp; (4) mời khách bấm Xem chi tiết/Thêm vào giỏ ở thẻ bên dưới; "
        "(5) hỏi lại một câu. Chỉ dùng sản phẩm trong danh sách trên."
    )
    try:
        if _is_anthropic():
            data = _post_anthropic(
                system=COMPOSE_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=settings.llm_max_tokens,
                temperature=0.3,
            )
            return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text").strip()
        data = _post_openai(
            messages=[
                {"role": "system", "content": COMPOSE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=settings.llm_max_tokens,
            temperature=0.3,
        )
        return (data["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:
        log.warning("compose vision failed: %s", exc)
        return ""


def _fallback_answer(infos: list[ImageInfo], listing: str) -> str:
    description = "; ".join(info.description for info in infos if info.description) or "Mình đã nhận ảnh của bạn."
    if not listing:
        return f"{description}\n\nMình chưa tìm thấy sản phẩm phù hợp trong ảnh. Bạn thử ảnh rõ hơn hoặc mô tả thêm nhé."
    return f"{description}\n\nSẢN PHẨM PHÙ HỢP TẠI MUADEE\n{listing}"


def _post_openai(messages: list[dict], max_tokens: int, temperature: float) -> dict:
    response = httpx.post(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {(settings.llm_api_key or settings.anthropic_api_key).strip()}"},
        json={
            "model": settings.llm_model or "claude-haiku-4-5",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        },
        timeout=max(settings.llm_timeout_seconds, 60),
    )
    response.raise_for_status()
    return response.json()


def _post_anthropic(system: str | None, messages: list[dict], max_tokens: int, temperature: float) -> dict:
    payload: dict = {
        "model": settings.llm_model or "claude-haiku-4-5",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        payload["system"] = system
    response = httpx.post(
        f"{settings.anthropic_base_url.rstrip('/')}/messages",
        headers={
            "x-api-key": (settings.llm_api_key or settings.anthropic_api_key).strip(),
            "anthropic-version": settings.anthropic_version,
            "content-type": "application/json",
        },
        json=payload,
        timeout=max(settings.llm_timeout_seconds, 60),
    )
    response.raise_for_status()
    return response.json()


def _is_anthropic() -> bool:
    return settings.llm_provider.strip().lower() in {"anthropic", "claude"}


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"images": [{"description": (text or "").strip()[:1000], "ocrText": "", "items": []}], "summary": (text or "")[:500]}


def _analyze_legacy(images: list[bytes], message: str, store_id: str | None) -> ImageAnalyzeResponse:
    infos: list[ImageInfo] = []
    product_ids: list[str] = []
    for data in images:
        matches = _safe(vision.visual_search, data, store_id, 5) or []
        text = _safe(vision.ocr_text, data) or ""
        caption = _safe(vision.describe, data) or ""
        for match in matches:
            if match["productId"] not in product_ids:
                product_ids.append(match["productId"])
        infos.append(ImageInfo(description=caption, ocrText=text, matches=[m["productId"] for m in matches]))

    query = " ".join([message] + [info.ocrText for info in infos]).strip()
    if query:
        for row in _safe(catalog.search, query, store_id, 5) or []:
            if row["productId"] not in product_ids:
                product_ids.append(row["productId"])
    product_ids = product_ids[:8]
    names = {d["productId"]: d for d in (_safe(catalog._load) or [])} if product_ids else {}
    listing = "\n".join(f"- {names[pid]['name']} ({_money(names[pid]['price'])}/{names[pid].get('unit') or 'sản phẩm'})" for pid in product_ids if pid in names)
    answer = _compose(infos, message, listing) or _fallback_answer(infos, listing)
    return ImageAnalyzeResponse(answer=answer, images=infos, productIds=product_ids)


def _money(value: object) -> str:
    try:
        return f"{int(float(value)):,}".replace(",", ".") + "đ"
    except (TypeError, ValueError):
        return "không rõ"


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        log.warning("%s failed: %s", getattr(fn, "__name__", "step"), exc)
        return None
