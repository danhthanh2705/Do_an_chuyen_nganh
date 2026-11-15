# Chatbot AI với Claude

## 1. Kiến trúc

```text
ChatWidget (React)
  → POST /api/v1/chat/sessions/:id/messages
    → ChatService (NestJS): lưu tin nhắn, lấy 10 tin gần nhất + 3 đơn gần nhất
      → AiClientService: POST {AI_SERVICE_URL}/chat (X-Internal-Key)
        → ai-service /chat
          → LLM_PROVIDER=anthropic: Claude Messages API + tool search_products
          → lỗi/chưa có key: fallback tìm sản phẩm theo từ khóa
      → hydrate sản phẩm theo storeId
  ← answer + products + needHandover
```

- Không có API key nào đi xuống frontend.
- Backend không gọi trực tiếp Anthropic; giữ AI service là nơi duy nhất chứa logic LLM.
- Lịch sử chat lưu ở `chatsessions`/`chatmessages`; chuyển nhân viên vẫn dùng luồng `HANDOVER` hiện có.

## 2. Cấu hình

Có 2 cách kết nối Claude:

### 2.1. Anthropic chính thức

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-...
LLM_MODEL=claude-haiku-4-5
LLM_MAX_TOKENS=1000
LLM_TIMEOUT_SECONDS=60
AI_SERVICE_URL=http://localhost:8000
AI_SERVICE_TIMEOUT_MS=60000
INTERNAL_API_KEY=khoa-noi-bo-backend-goi-ai
```

### 2.2. Relay tương thích OpenAI (khuyến nghị nếu dùng dịch vụ trung gian)

Nhiều relay cung cấp cả Anthropic Messages và OpenAI `/chat/completions`. Dùng đường OpenAI
thường nhanh hơn và tool-calling ổn định hơn:

```dotenv
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_BASE_URL=https://<relay-domain>/v1
LLM_MODEL=<model-id-lay-tu-/v1/models>
LLM_MAX_TOKENS=1000
LLM_TIMEOUT_SECONDS=60
```

Kiểm tra model relay hỗ trợ:

```bash
curl https://<relay-domain>/v1/models -H "Authorization: Bearer $KEY"
```

Ví dụ model Claude trên relay có thể là `claude-haiku-4-5`, `claude-sonnet-5-5`, `claude-opus-5`...
Phải dùng đúng `id` relay trả về.

Nếu relay hỗ trợ Anthropic Messages và muốn dùng trực tiếp:

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-...
ANTHROPIC_BASE_URL=https://<relay-domain>/v1
LLM_MODEL=claude-haiku-4-5
```

| Biến | Ý nghĩa |
|---|---|
| `LLM_PROVIDER` | `anthropic`/`claude`, `openai` (gồm relay OpenAI-compatible), `ollama` |
| `LLM_API_KEY` | Key cho provider `openai`/relay |
| `ANTHROPIC_API_KEY` | Key cho Anthropic/relay Anthropic; có thể dùng thay `LLM_API_KEY` |
| `LLM_BASE_URL` | Base URL OpenAI-compatible, mặc định `https://api.openai.com/v1` |
| `ANTHROPIC_BASE_URL` | Base URL Anthropic, mặc định `https://api.anthropic.com/v1` |
| `LLM_MODEL` | `claude-haiku-4-5` (nhanh/rẻ) hoặc model relay cung cấp |
| `LLM_MAX_TOKENS` | Giới hạn độ dài câu trả lời |
| `LLM_TIMEOUT_SECONDS` | Timeout gọi LLM |
| `AI_SERVICE_TIMEOUT_MS` | Timeout backend → AI service (phải ≥ LLM timeout) |

`INTERNAL_API_KEY` phải giống nhau ở backend và AI service (cùng đọc `.env`).

## 3. Phong cách trả lời

Chatbot tư vấn theo hướng dẫn dắt, không chỉ liệt kê sản phẩm:

1. Hiểu nhu cầu của khách.
2. Hướng dẫn chi tiết (với món ăn: nguyên liệu → cách làm từng bước → mẹo).
3. Gợi ý 2–4 sản phẩm thật đang bán tại Muadee (kèm giá/đơn vị).
4. Mời khách bấm **Xem chi tiết** / **Thêm vào giỏ** trên thẻ sản phẩm bên dưới.
5. Hỏi lại một câu để tiếp tục dẫn dắt.

Với câu hỏi công thức, AI bắt buộc gọi `check_ingredients` cho toàn bộ nguyên liệu (kể cả gia vị),
chỉ kết luận "chưa có" khi tool trả `available=false`, và chủ động đề xuất sản phẩm thay thế.
Nếu model quên phần gợi ý/CTA, AI service tự chèn (`_ensure_product_cta`) để trải nghiệm nhất quán.
Trước khi gọi model, AI service **pre-search** theo câu hỏi (`_presearch`) và đưa 2–3 sản phẩm phù hợp vào system prompt;
nhờ vậy câu hỏi mở như "chọn sữa cho bé" vẫn có gợi ý thật để dẫn dắt, kể cả khi model muốn hỏi thêm thông tin.
Ký hiệu markdown đơn giản được lọc bỏ để hiển thị đẹp trong ChatWidget (`white-space: pre-wrap`).

## 3. Gọi Claude

- Endpoint: `POST https://api.anthropic.com/v1/messages`
- Header: `x-api-key`, `anthropic-version: 2023-06-01`
- Body: `model`, `max_tokens`, `temperature`, `system`, `messages`, `tools`
- Tool `search_products` dùng `input_schema`; kết quả tool trả về dạng
  `{ "type": "tool_result", "tool_use_id": "...", "content": "<json sản phẩm>" }`.
- Tool `check_ingredients` kiểm tra **nhiều nguyên liệu cùng lúc** cho câu hỏi công thức, trả về
  `{ ingredient, available, products[] }`. Với câu hỏi nấu ăn, tool này được ưu tiên/ép gọi ở lượt đầu
  để tránh việc model kết luận "chưa có" mà không kiểm tra (ví dụ đường, dầu ăn, muối).
- Tối đa 6 vòng tool; hết vòng hoặc lỗi → fallback từ khóa.
- Kết quả tool gồm `name`, `price`, `unit`, `categoryName`, `inStock` để Claude trả lời kèm giá/đơn vị.

### Chất lượng tìm kiếm

- Chuẩn hóa tiếng Việt không dấu để so khớp, nhưng cộng điểm **cụm từ** (2–3 từ) để "cá hồi" xếp trên
  "cà chua"/"cà phê" (trước đây bị lẫn vì `cá` và `cà` cùng thành `ca`).
- Với câu hỏi nhiều từ, yêu cầu sản phẩm khớp **≥60% token** hoặc khớp cụm; nhờ đó "nước tương"
  không trả về "nước suối/nước cam", còn "nước mắm" vẫn khớp đúng.
- Cắt theo điểm tương đối (`score >= top * 0.42`) để loại kết quả chỉ trùng một token chung.
- Card sản phẩm cuối cùng chỉ giữ sản phẩm **được nhắc trong câu trả lời** (`_keep_relevant_ids`),
  tránh hiển thị sản phẩm nhiễu.

### System prompt

- Luôn trả lời tiếng Việt, ngắn gọn.
- Bắt buộc gọi `search_products` trước khi giới thiệu sản phẩm/giá.
- Không bịa sản phẩm.
- Nếu backend gửi `customerContext.recentOrders`, prompt bổ sung tối đa 3 đơn gần nhất
  (mã đơn, trạng thái đơn, trạng thái thanh toán, tổng tiền) để trả lời câu hỏi tra cứu đơn.

### Chuẩn hóa history

Backend có thể lưu role `staff`; Claude chỉ nhận `user`/`assistant`. Service map `staff → assistant`
và gộp các tin nhắn liên tiếp cùng role để tránh lỗi 400.

## 3.1. Phân tích ảnh (Claude Vision)

Frontend cho gửi tối đa 4 ảnh (JPG/PNG/WebP, ≤5 MB/ảnh). Luồng:

```text
ChatWidget → POST /chat/sessions/:id/images (multipart)
  → backend kiểm tra magic bytes, chuyển ảnh sang AI service
    → resize ≤1024px + JPEG quality 82
    → Claude Vision trích JSON: images[{description, ocrText, items[]}]
    → search catalog theo từng item (tối đa 6 truy vấn)
    → Claude soạn câu trả lời tư vấn + gợi ý sản phẩm
  → backend hydrate product cards + trả về chat
```

- Ảnh được gửi dạng base64 data URL (OpenAI-compatible) hoặc image block (Anthropic).
- Có OCR: chữ trong ảnh được đưa vào `ocrText` và dùng làm truy vấn tìm sản phẩm.
- Không cần cài `torch`/`easyocr`/`sentence-transformers` để dùng Claude Vision.
- Nếu Claude Vision lỗi, fallback pipeline CLIP/OCR cũ (nếu đã cài đặt) hoặc trả lời hướng dẫn.
- Ảnh không được lưu lại; chỉ dùng trong request hiện tại.

## 4. Fallback

| Tình huống | Hành vi |
|---|---|
| Chưa có `ANTHROPIC_API_KEY` | Bỏ qua Claude, tìm sản phẩm theo từ khóa |
| Claude lỗi mạng/401/429/5xx | Log warning, fallback từ khóa |
| Claude không trả text | Fallback từ khóa |
| Catalog/DB lỗi | Trả câu xin lỗi + `needHandover=true` |

Fallback vẫn trả `productIds` để backend hiển thị thẻ sản phẩm trong ChatWidget.

## 5. Chạy & kiểm thử

```bash
cd ai-service
python3 -m venv .venv
.venv/bin/pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "pydantic>=2.7" "pydantic-settings>=2.3" "pymongo>=4.8" "httpx>=0.27" python-multipart pillow numpy pytest
.venv/bin/python -m pytest -q   # 33 test
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Kiểm tra nhanh AI service (không cần key, sẽ fallback):

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Key: khoa-noi-bo-backend-goi-ai' \
  -d '{"sessionId":"s1","message":"Nguyên liệu nấu canh chua","storeId":"<storeId>"}'
```

Kiểm tra end-to-end qua backend:

```bash
SID=$(curl -s -X POST http://localhost:3000/api/v1/chat/sessions | jq -r .data.sessionId)
curl -X POST http://localhost:3000/api/v1/chat/sessions/$SID/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"Nguyên liệu nấu canh chua","storeId":"<storeId>"}'
```

Khi có key Claude, câu trả lời sẽ do Claude diễn đạt nhưng danh sách sản phẩm vẫn do
`search_products` (DB thật) quyết định.

## 6. Xử lý sự cố

| Triệu chứng | Kiểm tra |
|---|---|
| Chat luôn trả lời kiểu “Mình gợi ý cho bạn: ...” | Chưa có key/Claude lỗi → xem log `ai-service`: `Claude chat failed` |
| Nghi ngờ AI không gọi tool | Xem log `search_products query=... results=...`, `check_ingredients item=... results=...`, `Claude answer model=... rounds=... elapsed=...` |
| Trả lời thiếu nguyên liệu/giá | Kiểm tra `check_ingredients` có được gọi với đủ nguyên liệu; xem dữ liệu `products`/`inventories` của cửa hàng |
| `401` từ AI service | `INTERNAL_API_KEY` backend ≠ AI service |
| `401` từ Claude | Sai/thiếu `ANTHROPIC_API_KEY` |
| `400` từ Claude | Model sai hoặc history không hợp lệ (xem log warning) |
| Chat chậm > 60s | Giảm `LLM_MAX_TOKENS`, dùng `claude-haiku-4-5`, tăng `AI_SERVICE_TIMEOUT_MS` |
| Ảnh báo "chưa phân tích được ảnh" | Kiểm tra AI service còn sống, xem log `ai-service`; frontend không được tự set `Content-Type: multipart/form-data` (để browser tự thêm boundary); request ảnh có retry 1 lần khi lỗi mạng/5xx |
| Ảnh gửi lâu | Claude Vision gồm 2 lượt gọi (trích xuất + soạn trả lời), thường 15–40 giây; timeout backend/frontend 240 giây |
