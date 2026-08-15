import threading
import time
from io import BytesIO

import torch
from faster_whisper import WhisperModel


class ModelServices:
    def __init__(self, config):
        self.config = config
        self._vad_model = None
        self._image_pipe = None
        self._asr_model = None
        self._resolved_image_device = None
        self._image_compile_fallback_used = False
        self._vad_lock = threading.Lock()
        self._image_lock = threading.Lock()
        self._asr_lock = threading.Lock()

    def _resolve_torch_dtype(self, dtype_name: str):
        normalized = dtype_name.strip().lower()
        dtype_map = {
            "float32": torch.float32,
            "fp32": torch.float32,
            "float16": torch.float16,
            "fp16": torch.float16,
            "bfloat16": torch.bfloat16,
            "bf16": torch.bfloat16,
            "float8_e4m3fn": torch.float8_e4m3fn,
            "fp8": torch.float8_e4m3fn,
            "float8_e5m2": torch.float8_e5m2,
        }
        resolved = dtype_map.get(normalized)
        if resolved is None:
            raise RuntimeError(f"Unsupported image torch dtype: {dtype_name}")
        return resolved

    def get_vad_model(self):
        if self._vad_model is None:
            with self._vad_lock:
                if self._vad_model is None:
                    print("[VAD] Loading Silero VAD...")
                    self._vad_model, _ = torch.hub.load(
                        repo_or_dir="snakers4/silero-vad",
                        model="silero_vad",
                        force_reload=False,
                    )
                    print("[VAD] Silero VAD loaded.")
        return self._vad_model

    def _missing_image_dependency_message(self) -> str:
        return (
            "Image generation dependencies are missing. Install them with "
            "`.venv\\Scripts\\python.exe -m pip install diffusers transformers accelerate safetensors`."
        )

    def _is_missing_image_dependency_error(self, error: Exception) -> bool:
        error_text = str(error).lower()
        return (
            "requires the transformers library" in error_text
            or "no module named 'transformers'" in error_text
            or 'no module named "transformers"' in error_text
            or "no module named 'diffusers'" in error_text
            or 'no module named "diffusers"' in error_text
        )

    def get_image_device(self) -> str:
        if self._resolved_image_device is not None:
            return self._resolved_image_device

        configured_device = self.config.image_device.strip() or "cpu"
        resolved_device = configured_device
        if configured_device.startswith("cuda") and not torch.cuda.is_available():
            print(
                "[ZIT] CUDA was requested for image generation, but this Torch build "
                "does not have CUDA available. Falling back to CPU."
            )
            resolved_device = "cpu"

        self._resolved_image_device = resolved_device
        return resolved_device

    def get_image_pipe(self, use_compile: bool | None = None):
        if self._image_pipe is None:
            with self._image_lock:
                if self._image_pipe is None:
                    try:
                        from diffusers import (
                            FlowMatchEulerDiscreteScheduler,
                            ZImagePipeline,
                        )
                    except ImportError as e:
                        raise RuntimeError(self._missing_image_dependency_message()) from e
                    image_device = self.get_image_device()
                    compile_enabled = (
                        self.config.image_compile if use_compile is None else use_compile
                    )
                    print("[ZIT] Loading image pipeline...")
                    pipe_kwargs = {"low_cpu_mem_usage": False}
                    if image_device.startswith("cuda"):
                        pipe_kwargs["torch_dtype"] = self._resolve_torch_dtype(
                            self.config.image_torch_dtype
                        )
                    try:
                        self._image_pipe = ZImagePipeline.from_pretrained(
                            self.config.image_model_id,
                            **pipe_kwargs,
                        )
                    except Exception as e:
                        if self._is_missing_image_dependency_error(e):
                            raise RuntimeError(self._missing_image_dependency_message()) from e
                        raise
                    if self.config.image_sampler.lower() in {"euler_a", "euler-a", "euler a"}:
                        self._image_pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
                            self._image_pipe.scheduler.config,
                            stochastic_sampling=True,
                        )
                        print("[ZIT] Sampler set to Euler a compatible mode.")
                    self._image_pipe.to(image_device)
                    if image_device.startswith("cuda") and self.config.image_attention_backend:
                        try:
                            self._image_pipe.transformer.set_attention_backend(
                                self.config.image_attention_backend
                            )
                            print(
                                "[ZIT] Attention backend set to "
                                f"{self.config.image_attention_backend}."
                            )
                        except Exception as e:
                            print(f"[ZIT] Failed to set attention backend: {e}")
                    if image_device.startswith("cuda") and compile_enabled:
                        try:
                            self._image_pipe.transformer.compile()
                            print("[ZIT] Transformer compile enabled.")
                        except Exception as e:
                            print(f"[ZIT] Transformer compile failed: {e}")
                    print(
                        f"[ZIT] Image pipeline loaded on {image_device} "
                        f"(model={self.config.image_model_id}, dtype={self.config.image_torch_dtype})."
                    )
        return self._image_pipe

    def get_asr_model(self):
        if self._asr_model is None:
            with self._asr_lock:
                if self._asr_model is None:
                    print(f"[ASR] Loading Whisper model: {self.config.whisper_model}...")
                    self._asr_model = WhisperModel(
                        self.config.whisper_model,
                        device="cuda",
                        compute_type=self.config.whisper_compute_type,
                    )
                    print("[ASR] Whisper loaded.")
        return self._asr_model

    def generate_image_payload(self, prompt: str):
        image_pipe = self.get_image_pipe()
        started_at = time.perf_counter()
        try:
            img = image_pipe(
                prompt,
                width=self.config.image_width,
                height=self.config.image_height,
                num_inference_steps=self.config.image_steps,
                guidance_scale=self.config.image_guidance_scale,
            ).images[0]
        except Exception as e:
            error_text = str(e).lower()
            if (
                self.config.image_compile
                and not self._image_compile_fallback_used
                and "triton" in error_text
            ):
                print("[ZIT] Compile path failed due to Triton. Retrying without compile.")
                self._image_compile_fallback_used = True
                with self._image_lock:
                    self._image_pipe = None
                image_pipe = self.get_image_pipe(use_compile=False)
                started_at = time.perf_counter()
                img = image_pipe(
                    prompt,
                    width=self.config.image_width,
                    height=self.config.image_height,
                    num_inference_steps=self.config.image_steps,
                    guidance_scale=self.config.image_guidance_scale,
                ).images[0]
            else:
                raise
        elapsed = time.perf_counter() - started_at
        print(
            f"[ZIT] Image generation finished in {elapsed:.2f}s "
            f"(steps={self.config.image_steps}, size={self.config.image_width}x{self.config.image_height}, "
            f"cfg={self.config.image_guidance_scale}, sampler={self.config.image_sampler}, "
            f"device={self.get_image_device()})."
        )
        bio = BytesIO()
        bio.name = "generated.png"
        img.save(bio, "PNG")
        bio.seek(0)
        return bio
