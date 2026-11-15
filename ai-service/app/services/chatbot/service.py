import json
import logging
import re
import time

import httpx

from app.core.config import settings
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import catalog

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

SYSTEM_PROMPT = (
    "Bạn là trợ lý mua sắm của siêu thị Muadee, tư vấn như một nhân viên bán hàng tận tâm. "
    "Luôn trả lời bằng tiếng Việt tự nhiên, thân thiện; xưng 'mình' và gọi khách là 'bạn'. "
    "Quy trình tư vấn: (1) hiểu nhu cầu, (2) hướng dẫn chi tiết, (3) gợi ý sản phẩm thật đang bán, "
    "(4) hỏi lại một câu để dẫn dắt cuộc trò chuyện. "
    "Khi khách hỏi về sản phẩm, nguyên liệu, món ăn hoặc cách nấu, BẮT BUỘC gọi công cụ search_products "
    "hoặc check_ingredients trước khi trả lời. Với công thức/món ăn, BẮT BUỘC gọi check_ingredients với "
    "TOÀN BỘ nguyên liệu sẽ nhắc tới (kể cả đường, muối, dầu ăn, gia vị); chỉ nói 'chưa có' khi công cụ trả "
    "available=false và chủ động tìm sản phẩm thay thế đang bán. Chỉ giới thiệu sản phẩm do công cụ trả về, "
    "luôn kèm giá và đơn vị. Không bịa sản phẩm, giá hoặc tình trạng còn hàng. "
    "Với công thức nấu ăn, trình bày bằng tiêu đề viết hoa và gạch đầu dòng, theo đúng thứ tự: "
    "NGUYÊN LIỆU CÓ SẴN TẠI MUADEE (tên, giá/đơn vị); NGUYÊN LIỆU CÒN THIẾU (kèm sản phẩm thay thế đang bán nếu có); "
    "CÁCH LÀM (từng bước, nêu thời gian, lửa, dấu hiệu hoàn thành); MẸO NGON HƠN (2-3 mẹo); "
    "GỢI Ý TỪ MUADEE (2-4 sản phẩm phù hợp nhất kèm giá/đơn vị). "
    "Sau phần gợi ý, mời khách bấm 'Xem chi tiết' hoặc 'Thêm vào giỏ' ở các thẻ sản phẩm bên dưới và hỏi "
    "một câu dẫn dắt (ví dụ: 'Bạn muốn mình gợi ý món khác từ cá hồi không?'). "
    "Với câu hỏi sản phẩm thông thường, giải thích tiêu chí chọn/cách dùng rồi gợi ý 2-3 sản phẩm phù hợp nhất. "
    "Nếu cần hỏi thêm thông tin, vẫn gọi search_products cho nhu cầu chính và gợi ý 1-2 sản phẩm hiện có để khách "
    "tham khảo trước; không kết thúc bằng câu hỏi suông. "
    "Không hứa tìm cửa hàng/nguồn bán ngoài Muadee; với nguyên liệu thiếu, chỉ nói khách có thể mua ở nơi khác và "
    "gợi ý sản phẩm thay thế đang bán tại Muadee. Không dùng từ tiếng Anh khi có từ tiếng Việt tương đương. Trả lời đủ ý nhưng không quá 320 từ. "
    "Nếu không có thông tin hoặc khách cần người hỗ trợ, nói rõ và đề nghị chuyển nhân viên."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Chỉ dùng khi khách hỏi một sản phẩm cụ thể. Không dùng cho câu hỏi công thức/món ăn.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Từ khóa sản phẩm, ví dụ 'sữa tươi', 'rau canh chua', 'nước tương'"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_ingredients",
            "description": "BẮT BUỘC dùng cho mọi câu hỏi công thức/món ăn. Kiểm tra cùng lúc toàn bộ nguyên liệu (kể cả gia vị) có bán tại cửa hàng hay không.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Danh sách nguyên liệu cần kiểm tra, ví dụ ['cá hồi', 'nước tương', 'gừng', 'tỏi', 'đường']",
                    }
                },
                "required": ["items"],
            },
        },
    },
]

ANTHROPIC_TOOLS = [
    {
        "name": "search_products",
        "description": "Chỉ dùng khi khách hỏi một sản phẩm cụ thể. Không dùng cho câu hỏi công thức/món ăn.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Từ khóa sản phẩm, ví dụ 'sữa tươi', 'rau canh chua', 'nước tương'"}},
            "required": ["query"],
        },
    },
    {
        "name": "check_ingredients",
        "description": "BẮT BUỘC dùng cho mọi câu hỏi công thức/món ăn. Kiểm tra cùng lúc toàn bộ nguyên liệu (kể cả gia vị) có bán tại cửa hàng hay không.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Danh sách nguyên liệu cần kiểm tra, ví dụ ['cá hồi', 'nước tương', 'gừng', 'tỏi', 'đường']",
                }
            },
            "required": ["items"],
        },
    },
]

ORDER_STATUS_LABELS = {
    "PENDING": "chờ xác nhận",
    "PREPARING": "đang chuẩn bị",
    "READY_FOR_PICKUP": "sẵn sàng nhận tại cửa hàng",
    "SHIPPING": "đang giao",
    "DELIVERED": "đã giao",
    "COMPLETED": "hoàn thành",
    "CANCELLED": "đã hủy",
    "RETURNING": "đang trả hàng",
    "RETURNED": "đã trả hàng",
}
PAYMENT_STATUS_LABELS = {
    "PENDING": "chưa thanh toán",
    "SUCCESS": "đã thanh toán",
    "PAID": "đã thanh toán",
    "FAILED": "thanh toán thất bại",
    "EXPIRED": "hết hạn thanh toán",
    "PARTIALLY_PAID": "thanh toán thiếu",
    "OVERPAID": "thanh toán thừa",
    "REFUNDED": "đã hoàn tiền",
    "REVIEW_REQUIRED": "đang kiểm tra thanh toán",
}


def _format_money(value: object) -> str:
    try:
        return f"{int(float(value)):,}".replace(",", ".") + "đ"
    except (TypeError, ValueError):
        return "không rõ"


def _system_prompt(req: ChatRequest) -> str:
    prompt = SYSTEM_PROMPT
    context = req.customerContext or {}
    store_name = context.get("storeName")
    if isinstance(store_name, str) and store_name.strip():
        prompt += f"\n\nCửa hàng đang tư vấn: {store_name.strip()}."
    orders = context.get("recentOrders") or []
    if isinstance(orders, list) and orders:
        lines = []
        for order in orders[:3]:
            if not isinstance(order, dict):
                continue
            status = ORDER_STATUS_LABELS.get(str(order.get("status")), str(order.get("status") or "không rõ"))
            payment = PAYMENT_STATUS_LABELS.get(
                str(order.get("paymentStatus")), str(order.get("paymentStatus") or "không rõ")
            )
            lines.append(
                f"- Đơn {order.get('orderCode', '')}: {status}, {payment}, "
                f"tổng {_format_money(order.get('totalAmount'))}"
            )
        if lines:
            prompt += (
                "\n\nThông tin đơn hàng gần nhất của khách (chỉ dùng để trả lời câu hỏi về đơn hàng, "
                "không suy diễn thêm, không tiết lộ dữ liệu của khách khác):\n" + "\n".join(lines)
            )
    return prompt


def _keep_relevant_ids(answer: str, ids: list[str], names: dict[str, str]) -> list[str]:
    """Chi tra ve card san pham duoc nhac trong cau tra loi cuoi cung.

    Tool search co the tra ve nhieu san pham (ke ca nhieu), nhung UI chi nen hien
    nhung san pham ma Claude thuc su gioi thieu de tranh nhieu.
    """
    unique = list(dict.fromkeys(ids))
    answer_norm = catalog.normalize(answer)
    kept = [
        product_id for product_id in unique
        if names.get(product_id) and catalog.normalize(names[product_id]) in answer_norm
    ]
    return kept or unique[:3]


def _run_tool(name: str, args: dict, req: ChatRequest, product_ids: list[str], names: dict[str, str]):
    """Thuc thi tool do LLM yeu cau va cap nhat danh sach san pham da tim thay."""
    if name == "check_ingredients":
        items = args.get("items") or []
        if not isinstance(items, list):
            items = [str(items)]
        results = []
        for item in items[:10]:
            ingredient = str(item).strip()
            if not ingredient:
                continue
            rows = catalog.search(ingredient, req.storeId, 4)
            log.info("check_ingredients item=%r results=%d", ingredient, len(rows))
            for row in rows:
                names[row["productId"]] = row["name"]
                if row["productId"] not in product_ids:
                    product_ids.append(row["productId"])
            results.append({"ingredient": ingredient, "available": bool(rows), "products": rows})
        return results

    query = str(args.get("query") or req.message)
    rows = catalog.search(query, req.storeId, 5)
    log.info("search_products query=%r results=%d", query, len(rows))
    for row in rows:
        names[row["productId"]] = row["name"]
        if row["productId"] not in product_ids:
            product_ids.append(row["productId"])
    return rows


COOKING_KEYWORDS = (
    "cách", "nấu", "nau", "chế biến", "che bien", "món", "mon", "công thức", "cong thuc",
    "làm", "lam", "ngâm", "ngam", "ướp", "uop", "chiên", "chien", "nướng", "nuong", "hấp", "hap",
)


def _is_cooking_question(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in COOKING_KEYWORDS)


def _clean_answer(text: str) -> str:
    """Bo ky hieu markdown don gian vi ChatWidget hien thi text thuong."""
    cleaned = re.sub(r"(?m)^#{1,6}\s*", "", text or "")
    cleaned = cleaned.replace("**", "").replace("__", "").replace("*", "")
    return cleaned.strip()


def _presearch(req: ChatRequest, product_ids: list[str], names: dict[str, str]) -> str:
    """Tra cuu nhanh theo cau hoi de chatbot luon co san goi y san pham.

    Model co the bo qua tool khi hoi mo (vi du "chon sua cho be"); presearch giup
    van co du lieu that de dan dat va hien thi card san pham.
    """
    rows = catalog.search(req.message, req.storeId, 3)
    if not rows:
        return ""
    log.info("presearch query=%r results=%d", req.message[:80], len(rows))
    lines = []
    for row in rows:
        names[row["productId"]] = row["name"]
        if row["productId"] not in product_ids:
            product_ids.append(row["productId"])
        lines.append(f"- {row['name']}: {_format_money(row['price'])}/{row.get('unit') or 'sản phẩm'}")
    return "\n\nKết quả tra cứu nhanh cho câu hỏi hiện tại (dùng nếu phù hợp):\n" + "\n".join(lines)


def _ensure_product_cta(answer: str, product_ids: list[str], names: dict[str, str]) -> str:
    """Dam bao cau tra loi luon co loi moi xem/them san pham trong web.

    Model co the quen muc goi y; chen thu cong de giu trai nghiem nhat quan ma
    khong lap lai neu model da viet muc nay.
    """
    ids = [product_id for product_id in dict.fromkeys(product_ids) if names.get(product_id)]
    if not ids:
        return answer

    text = (answer or "").rstrip()
    lowered = text.lower()
    has_cta = "thêm vào giỏ" in lowered or "xem chi tiết" in lowered
    has_section = any(marker in lowered for marker in (
        "gợi ý từ muadee", "sản phẩm tại muadee", "sản phẩm phù hợp tại muadee",
    ))
    if not has_cta and not has_section:
        listing = "\n".join(f"- {names[product_id]}" for product_id in ids[:4])
        text += "\n\nGỢI Ý TỪ MUADEE\n" + listing
    if not has_cta:
        text += "\n\nBạn có thể bấm 'Xem chi tiết' hoặc 'Thêm vào giỏ' ở các thẻ sản phẩm bên dưới nhé."
    return text


def _fallback(req: ChatRequest) -> ChatResponse:
    rows = catalog.search(req.message, req.storeId, 4)
    if not rows:
        return ChatResponse(answer="Mình chưa tìm thấy sản phẩm phù hợp. Bạn có thể mô tả cụ thể hơn hoặc chuyển sang nhân viên hỗ trợ.", needHandover=False)
    names = ", ".join(r["name"] for r in rows)
    return ChatResponse(answer=f"Mình gợi ý cho bạn: {names}.", productIds=[r["productId"] for r in rows])


def _llm(req: ChatRequest) -> ChatResponse:
    product_ids: list[str] = []
    names: dict[str, str] = {}
    presearch = _presearch(req, product_ids, names)
    system_prompt = _system_prompt(req) + presearch
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": "user" if m.role == "user" else "assistant", "content": m.content} for m in req.history]
    messages.append({"role": "user", "content": req.message})
    headers = {"Authorization": f"Bearer {settings.llm_api_key.strip()}"}
    model = settings.llm_model or "gpt-4o-mini"
    started = time.monotonic()

    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        for round_index in range(6):
            payload = {"model": model, "messages": messages, "tools": TOOLS}
            if round_index == 0 and _is_cooking_question(req.message):
                payload["tool_choice"] = {"type": "function", "function": {"name": "check_ingredients"}}
            r = client.post(f"{settings.llm_base_url.rstrip('/')}/chat/completions", headers=headers,
                            json=payload)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            calls = msg.get("tool_calls")
            if not calls:
                answer = msg.get("content") or ""
                log.info("LLM answer model=%s rounds=%d products=%d elapsed=%.1fs",
                         model, round_index + 1, len(product_ids), time.monotonic() - started)
                kept = _keep_relevant_ids(answer, product_ids, names)
                return ChatResponse(answer=_ensure_product_cta(_clean_answer(answer), kept, names), productIds=kept)
            messages.append(msg)
            for call in calls:
                args = json.loads(call["function"]["arguments"] or "{}")
                payload = _run_tool(
                    call["function"].get("name", ""), args, req, product_ids, names
                )
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(payload, ensure_ascii=False)})
    return _fallback(req)


def _ollama(req: ChatRequest) -> ChatResponse:
    # Model nho goi tool khong on dinh: tim san pham truoc, roi de model dien dat.
    rows = catalog.search(req.message, req.storeId, 5)
    if not rows:
        return _fallback(req)
    listing = "\n".join(
        f"- {r['name']} ({r['price']:,.0f}đ/{r.get('unit') or 'sản phẩm'}, {r.get('categoryName') or 'danh mục khác'})"
        for r in rows
    )
    system = _system_prompt(req).split(" Khi khách")[0] + (
        " Chỉ giới thiệu các sản phẩm trong danh sách sau, không thêm sản phẩm khác, tối đa 3 câu.\n" + listing
    )
    messages = [{"role": "system", "content": system}]
    messages += [{"role": "user" if m.role == "user" else "assistant", "content": m.content} for m in req.history[-6:]]
    messages.append({"role": "user", "content": f"{req.message}\n\nDanh sách sản phẩm có sẵn:\n    {listing}\n\nHãy giới thiệu ngắn gọn các sản phẩm trên bằng tiếng Việt."})
    r = httpx.post(f"{settings.ollama_url}/api/chat", timeout=settings.llm_timeout_seconds,
                   json={"model": settings.llm_model or "llama3.2:1b", "messages": messages, "stream": False})
    r.raise_for_status()
    text = r.json()["message"]["content"].strip()
    names = {row["productId"]: row["name"] for row in rows}
    kept = _keep_relevant_ids(text or "", [x["productId"] for x in rows], names)
    return ChatResponse(
        answer=_ensure_product_cta(_clean_answer(text or "Mình gợi ý các sản phẩm bên dưới."), kept, names),
        productIds=kept,
    )


def _anthropic_messages(req: ChatRequest) -> list[dict]:
    """Chuyen history cua backend sang messages API cua Claude.

    Anthropic yeu cau role user/assistant; backend co the co role 'staff' hoac
    nhieu tin nhan lien tiep cung vai tro, nen gop lai de tranh loi 400.
    """
    messages: list[dict] = []

    def push(role: str, content: str) -> None:
        text = (content or "").strip()
        if not text:
            return
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] = f"{messages[-1]['content']}\n{text}"
        else:
            messages.append({"role": role, "content": text})

    for item in req.history:
        push("user" if item.role == "user" else "assistant", item.content)
    push("user", req.message)
    if not messages:
        messages.append({"role": "user", "content": req.message})
    return messages


def _anthropic(req: ChatRequest) -> ChatResponse:
    api_key = (settings.llm_api_key or settings.anthropic_api_key).strip()
    if not api_key:
        raise RuntimeError("Chưa cấu hình ANTHROPIC_API_KEY/LLM_API_KEY cho Claude")

    model = (settings.llm_model or "claude-haiku-4-5").strip()
    headers = {
        "x-api-key": api_key,
        "anthropic-version": settings.anthropic_version,
        "content-type": "application/json",
    }
    messages = _anthropic_messages(req)
    product_ids: list[str] = []
    names: dict[str, str] = {}
    system_prompt = _system_prompt(req) + _presearch(req, product_ids, names)
    started = time.monotonic()

    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        # Claude co the yeu cau goi tool nhieu vong (moi nguyen lieu mot lan).
        for round_index in range(6):
            payload = {
                "model": model,
                "max_tokens": settings.llm_max_tokens,
                "temperature": 0.3,
                "system": system_prompt,
                "messages": messages,
                "tools": ANTHROPIC_TOOLS,
            }
            if len(messages) == 1 and _is_cooking_question(req.message):
                payload["tool_choice"] = {"type": "tool", "name": "check_ingredients"}
            response = client.post(
                f"{settings.anthropic_base_url.rstrip('/')}/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            blocks = response.json().get("content", [])
            text = "".join(
                block.get("text", "") for block in blocks if block.get("type") == "text"
            ).strip()
            tool_uses = [block for block in blocks if block.get("type") == "tool_use"]

            if not tool_uses:
                log.info("Claude answer model=%s rounds=%d products=%d elapsed=%.1fs",
                         model, round_index + 1, len(product_ids), time.monotonic() - started)
                if text:
                    kept = _keep_relevant_ids(text, product_ids, names)
                    return ChatResponse(answer=_ensure_product_cta(_clean_answer(text), kept, names), productIds=kept)
                return _fallback(req)

            # Gui lai nguyen van block assistant (gồm text + tool_use) de Claude
            # biet tool nao dang duoc goi, sau do tra ket qua tool.
            messages.append({"role": "assistant", "content": blocks})
            tool_results = []
            for tool in tool_uses:
                payload = _run_tool(
                    str(tool.get("name", "")), tool.get("input") or {}, req, product_ids, names
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool.get("id", ""),
                    "content": json.dumps(payload, ensure_ascii=False),
                })
            messages.append({"role": "user", "content": tool_results})

    return _fallback(req)


def answer(req: ChatRequest) -> ChatResponse:
    provider = settings.llm_provider.strip().lower()
    if provider == "ollama":
        try:
            return _ollama(req)
        except Exception as exc:
            log.warning("Ollama chat failed: %s", exc)
    elif provider in {"anthropic", "claude"} and (settings.llm_api_key or settings.anthropic_api_key).strip():
        try:
            return _anthropic(req)
        except Exception as exc:  # Loi API Claude -> van tra loi bang tim kiem tu khoa
            log.warning("Claude chat failed: %s", exc)
    elif provider == "openai" and (settings.llm_api_key or settings.anthropic_api_key).strip():
        try:
            return _llm(req)
        except Exception as exc:  # LLM/DB loi -> van tra loi bang tim kiem tu khoa
            log.warning("LLM chat failed: %s", exc)
    try:
        return _fallback(req)
    except Exception as exc:
        log.warning("Catalog search failed: %s", exc)
        return ChatResponse(answer="Hiện mình chưa tra cứu được sản phẩm. Bạn thử lại sau hoặc chuyển sang nhân viên hỗ trợ.", needHandover=True)
