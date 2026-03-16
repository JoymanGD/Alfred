@echo off
chcp 65001 > nul

set "ALFRED_OLLAMA_MODEL=gemma3:12b"
set "ALFRED_IMAGE_MODEL_ID=Tongyi-MAI/Z-Image-Turbo"
set "ALFRED_IMAGE_TORCH_DTYPE=bf16"
set "ALFRED_IMAGE_DEVICE=cuda"
set "ALFRED_IMAGE_STEPS=8"
set "ALFRED_IMAGE_WIDTH=512"
set "ALFRED_IMAGE_HEIGHT=512"
set "ALFRED_IMAGE_GUIDANCE_SCALE=0"
set "ALFRED_IMAGE_SAMPLER=euler_a"
set "ALFRED_IMAGE_ATTENTION_BACKEND="
set "ALFRED_IMAGE_COMPILE=1"

echo Starting Alfred with model: %ALFRED_OLLAMA_MODEL%
python alfred-bot.py

echo Alfred stopped.
pause
