import logging

from fastapi import Depends, FastAPI

from app.core.security import verify_internal_key
from app.routers import chatbot, image, recommendation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="AI Service - Supermarket B2C", version="0.1.0")


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "service": "ai-service"}


# Moi router deu bat buoc header X-Internal-Key (chi backend goi)
guard = [Depends(verify_internal_key)]
app.include_router(chatbot.router, dependencies=guard)
app.include_router(recommendation.router, dependencies=guard)
app.include_router(image.router, dependencies=guard)
