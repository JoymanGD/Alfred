"""Load the models required for chat and speech before accepting input."""


def preload_components(decider, ollama_model, preload_ollama_model, audio_engine, ocr_engine=None, vision_model=None, vision_options=None):
    components = (
        ("Decider", decider.warmup),
        ("Gemma 4", lambda: preload_ollama_model(ollama_model)),
        ("OmniVoice and session voice", audio_engine.warmup_tts),
    )
    if vision_model and vision_model != ollama_model:
        components += (("Vision model", lambda: preload_ollama_model(vision_model, options=vision_options)),)
    if ocr_engine is not None:
        components += (("Document OCR", ocr_engine.warmup),)
    for name, load in components:
        print(f"[START] Loading {name}...")
        load()
        print(f"[START] {name} ready.")
