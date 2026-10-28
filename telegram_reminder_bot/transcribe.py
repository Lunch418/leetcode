"""Локальная расшифровка голосовых через faster-whisper (бесплатно, без внешних API)."""

import asyncio
import io
import logging

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, model_size: str):
        self.model_size = model_size
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Загружаю модель whisper '%s'...", self.model_size)
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    def _transcribe(self, audio: bytes) -> str:
        segments, _ = self._load().transcribe(io.BytesIO(audio), language="ru", vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, audio: bytes) -> str:
        # Расшифровка блокирующая — уводим её из event loop
        return await asyncio.to_thread(self._transcribe, audio)
