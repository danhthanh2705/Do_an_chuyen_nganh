import json

import pytest

from app.schemas.chat import ChatMessage, ChatRequest
from app.services.chatbot import service


def make_request(**overrides):
    data = {
        "sessionId": "s1",
        "message": "Nguyên liệu nấu canh chua",
        "storeId": "store-1",
        "history": [],
        "customerContext": None,
    }
    data.update(overrides)
    return ChatRequest(**data)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses, calls):
        self._responses = list(responses)
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        self._calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return self._responses.pop(0)


def patch_client(monkeypatch, responses, calls):
    monkeypatch.setattr(service.httpx, "Client", lambda *a, **k: FakeClient(responses, calls))


def patch_settings(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setattr(service.settings, key, value)


def test_missing_api_key_raises(monkeypatch):
    patch_settings(monkeypatch, llm_api_key="", anthropic_api_key="")
    with pytest.raises(RuntimeError):
        service._anthropic(make_request())


def test_claude_returns_text(monkeypatch):
    calls = []
    patch_settings(monkeypatch, llm_api_key="sk-ant-test", anthropic_api_key="", llm_model="claude-sonnet-4-5")
    patch_client(monkeypatch, [FakeResponse({"content": [{"type": "text", "text": "Chào bạn!"}]})], calls)

    result = service._anthropic(make_request())

    assert result.answer == "Chào bạn!"
    assert calls[0]["url"].endswith("/messages")
    assert calls[0]["headers"]["x-api-key"] == "sk-ant-test"
    assert calls[0]["headers"]["anthropic-version"] == service.settings.anthropic_version
    assert calls[0]["json"]["model"] == "claude-sonnet-4-5"
    assert calls[0]["json"]["max_tokens"] == service.settings.llm_max_tokens


def test_claude_tool_use_loop(monkeypatch):
    calls = []
    patch_settings(monkeypatch, llm_api_key="sk-ant-test", anthropic_api_key="")
    responses = [
        FakeResponse({"content": [
            {"type": "text", "text": "Để mình tìm nguyên liệu."},
            {"type": "tool_use", "id": "tool-1", "name": "search_products", "input": {"query": "cà chua"}},
        ]}),
        FakeResponse({"content": [{"type": "text", "text": "Bạn có thể dùng cà chua và rau muống."}]}),
    ]
    patch_client(monkeypatch, responses, calls)
    monkeypatch.setattr(service.catalog, "search", lambda query, store_id, limit: [
        {"productId": "p1", "name": "Cà chua", "price": 10000},
        {"productId": "p2", "name": "Rau muống", "price": 8000},
    ])

    result = service._anthropic(make_request())

    assert "Bạn có thể dùng cà chua và rau muống." in result.answer
    assert result.productIds == ["p1", "p2"]
    assert len(calls) == 2
    second_messages = calls[1]["json"]["messages"]
    assert second_messages[-1]["role"] == "user"
    tool_result = second_messages[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "tool-1"
    assert json.loads(tool_result["content"])[0]["productId"] == "p1"


def test_claude_falls_back_after_tool_limit(monkeypatch):
    calls = []
    patch_settings(monkeypatch, llm_api_key="sk-ant-test", anthropic_api_key="")
    responses = [
        FakeResponse({"content": [{"type": "tool_use", "id": f"t{i}", "name": "search_products", "input": {"query": "sữa"}}]})
        for i in range(6)
    ]
    patch_client(monkeypatch, responses, calls)
    monkeypatch.setattr(service.catalog, "search", lambda query, store_id, limit: [
        {"productId": "p9", "name": "Sữa tươi", "price": 20000},
    ])

    result = service._anthropic(make_request(message="sữa"))

    assert len(calls) == 6
    assert "Sữa tươi" in result.answer
    assert result.productIds == ["p9"]


def test_messages_are_normalized_for_claude():
    req = make_request(
        history=[
            ChatMessage(role="user", content="Xin chào"),
            ChatMessage(role="staff", content="Mình là nhân viên"),
            ChatMessage(role="assistant", content="Mình có thể giúp gì?"),
        ],
        message="Tư vấn sữa",
    )
    messages = service._anthropic_messages(req)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "Mình là nhân viên" in messages[1]["content"]
    assert "Mình có thể giúp gì?" in messages[1]["content"]
    assert messages[2]["content"] == "Tư vấn sữa"


def test_system_prompt_contains_customer_orders():
    req = make_request(customerContext={"recentOrders": [{
        "orderCode": "MD261113001",
        "status": "PENDING",
        "paymentStatus": "SUCCESS",
        "totalAmount": 93000,
    }]})
    prompt = service._system_prompt(req)
    assert "MD261113001" in prompt
    assert "chờ xác nhận" in prompt
    assert "đã thanh toán" in prompt
    assert "93.000đ" in prompt


def test_system_prompt_contains_store_name():
    prompt = service._system_prompt(make_request(customerContext={"storeName": "Muadee Nguyễn Trãi"}))
    assert "Muadee Nguyễn Trãi" in prompt


def test_keep_relevant_ids_filters_products_not_in_answer():
    ids = ["p1", "p2", "p3"]
    names = {"p1": "Cá hồi Na Uy", "p2": "Cà chua", "p3": "Cà phê sữa lon"}
    answer = "Bạn nên mua Cá hồi Na Uy và Cà chua để nấu canh."
    assert service._keep_relevant_ids(answer, ids, names) == ["p1", "p2"]


def test_keep_relevant_ids_falls_back_to_first_three():
    ids = ["p1", "p2", "p3", "p4"]
    names = {"p1": "Cá hồi Na Uy", "p2": "Cà chua", "p3": "Cà phê sữa", "p4": "Sữa tươi"}
    assert service._keep_relevant_ids("Không có tên sản phẩm nào.", ids, names) == ["p1", "p2", "p3"]


def test_answer_uses_claude_then_falls_back(monkeypatch):
    patch_settings(monkeypatch, llm_provider="anthropic", llm_api_key="sk-ant-test", anthropic_api_key="")

    def boom(req):
        raise RuntimeError("network")

    monkeypatch.setattr(service, "_anthropic", boom)
    monkeypatch.setattr(service, "_fallback", lambda req: service.ChatResponse(answer="fallback", productIds=[]))
    assert service.answer(make_request()).answer == "fallback"


def test_answer_skips_claude_without_key(monkeypatch):
    patch_settings(monkeypatch, llm_provider="anthropic", llm_api_key="", anthropic_api_key="")
    called = {"value": False}

    def tracked(req):
        called["value"] = True
        return service.ChatResponse(answer="claude")

    monkeypatch.setattr(service, "_anthropic", tracked)
    monkeypatch.setattr(service, "_fallback", lambda req: service.ChatResponse(answer="fallback", productIds=[]))

    assert service.answer(make_request()).answer == "fallback"
    assert called["value"] is False


def test_check_ingredients_tool_checks_each_item(monkeypatch):
    def fake_search(query, store_id, limit):
        if query == "đường":
            return [{"productId": "p-duong", "name": "Đường tinh luyện", "price": 25000, "unit": "kg"}]
        return []

    monkeypatch.setattr(service.catalog, "search", fake_search)
    ids: list[str] = []
    names: dict[str, str] = {}
    result = service._run_tool(
        "check_ingredients",
        {"items": ["đường", "gừng", "nước tương"]},
        make_request(),
        ids,
        names,
    )
    assert [item["ingredient"] for item in result] == ["đường", "gừng", "nước tương"]
    assert [item["available"] for item in result] == [True, False, False]
    assert ids == ["p-duong"]
    assert names == {"p-duong": "Đường tinh luyện"}


def test_ensure_product_cta_adds_section_and_invitation():
    answer = "Cá hồi rất tốt cho sức khỏe."
    text = service._ensure_product_cta(answer, ["p1"], {"p1": "Cá hồi Na Uy"})
    assert "GỢI Ý TỪ MUADEE" in text
    assert "Cá hồi Na Uy" in text
    assert "Thêm vào giỏ" in text


def test_ensure_product_cta_does_not_duplicate():
    answer = "GỢI Ý TỪ MUADEE\n- Cá hồi Na Uy\nBạn có thể bấm Thêm vào giỏ ở thẻ bên dưới."
    text = service._ensure_product_cta(answer, ["p1"], {"p1": "Cá hồi Na Uy"})
    assert text == answer


def test_ensure_product_cta_no_products_keeps_answer():
    assert service._ensure_product_cta("Chào bạn", [], {}) == "Chào bạn"


def test_presearch_adds_products_and_context(monkeypatch):
    monkeypatch.setattr(service.catalog, "search", lambda q, s, l: [
        {"productId": "p1", "name": "Sữa tươi ít đường", "price": 148000, "unit": "chai"},
    ])
    ids: list[str] = []
    names: dict[str, str] = {}
    text = service._presearch(make_request(message="sữa cho bé"), ids, names)
    assert "Sữa tươi ít đường" in text
    assert "148.000đ/chai" in text
    assert ids == ["p1"]


def test_ensure_product_cta_skips_section_when_model_already_invites():
    answer = "SẢN PHẨM PHỤC VỤ BẠN\n- Cà chua\nBạn bấm Xem chi tiết hoặc Thêm vào giỏ bên dưới nhé."
    text = service._ensure_product_cta(answer, ["p1"], {"p1": "Cà chua"})
    assert "GỢI Ý TỪ MUADEE" not in text
    assert text == answer
