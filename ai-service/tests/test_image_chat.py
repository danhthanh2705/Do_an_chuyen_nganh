import base64
import io
import json

from PIL import Image

from app.services import image_chat


def test_parse_json_strips_code_fence():
    payload = {"images": [{"description": "cà chua", "ocrText": "", "items": ["cà chua"]}]}
    parsed = image_chat._parse_json("```json\n" + json.dumps(payload) + "\n```")
    assert parsed["images"][0]["items"] == ["cà chua"]


def test_extract_info_builds_queries():
    infos, queries = image_chat._extract_info({
        "images": [
            {"description": "quả cà chua", "ocrText": "CA CHUA", "items": ["cà chua", "hành tím"]},
        ]
    }, "")
    assert infos[0].description == "quả cà chua"
    assert infos[0].ocrText == "CA CHUA"
    assert queries == ["cà chua", "hành tím"]


def test_extract_info_falls_back_to_message():
    infos, queries = image_chat._extract_info({"summary": "ảnh mờ"}, "gợi ý món từ ảnh")
    assert infos[0].description == "ảnh mờ"
    assert queries == ["gợi ý món từ ảnh"]


def test_prepare_image_returns_jpeg_base64():
    buffer = io.BytesIO()
    Image.new("RGB", (2000, 1200), "red").save(buffer, format="PNG")
    encoded = image_chat._prepare_image(buffer.getvalue())
    raw = base64.b64decode(encoded)
    image = Image.open(io.BytesIO(raw))
    assert image.format == "JPEG"
    assert max(image.size) <= image_chat.MAX_SIDE
