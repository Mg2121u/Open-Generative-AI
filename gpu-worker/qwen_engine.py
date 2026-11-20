"""Qwen-Image-Edit-2511 24GB-GPU pilot backend.

Apache-2.0 base weights; transformer uses Q4_K_M GGUF quantization and the
Qwen2.5-VL text encoder is loaded in 4-bit NF4 to reduce peak VRAM.
This is NOT a GPU-validated configuration: memory, upstream loading changes
or image quality may still fail on a Salad RTX 3090/24GB RAM node.
"""
from __future__ import annotations

import logging
import secrets

MODEL_ID = "Qwen/Qwen-Image-Edit-2511"
GGUF_REPO = "unsloth/Qwen-Image-Edit-2511-GGUF"
GGUF_FILENAME = "qwen-image-edit-2511-Q4_K_M.gguf"
log = logging.getLogger("qwen-engine")


def qwen_inputs(prompt, references, width, height):
    """Construct the officially documented multi-image edit call parameters."""
    if not references:
        raise ValueError("Qwen Image Edit requires one identity or scenery reference")
    if len(references) > 2:
        raise ValueError("At most two reference photos are supported")
    images = [image for role, image in references]
    return {
        "image": images,
        "prompt": prompt,
        "true_cfg_scale": 4.0,
        "negative_prompt": " ",
        "num_inference_steps": 28,
        "guidance_scale": 1.0,
        "num_images_per_prompt": 1,
        "width": width,
        "height": height,
    }


def load_qwen_engine():
    """Initialize a quantized Qwen editing pipeline; no model downloaded at build time.

    Keep the 4-bit text encoder and Q4 GGUF transformer loaded to GPU at once.
    Avoid a 40+ GB full-precision model on the 24GB Salad pilot GPU. All model
    imports are lazy so the unit tests can run without CUDA or model downloads.
    """
    import torch
    from diffusers import QwenImageEditPlusPipeline, QwenImageTransformer2DModel, GGUFQuantizationConfig
    from huggingface_hub import hf_hub_download
    from transformers import BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration

    if not torch.cuda.is_available():
        raise RuntimeError("Qwen requires an NVIDIA CUDA GPU")
    vram = torch.cuda.get_device_properties(0).total_memory
    if vram < 20 * 1024**3:
        raise RuntimeError("Qwen-Image-Edit-2511 Q4+NF4 pilot requires at least 20 GiB GPU VRAM")

    log.info("Initializing Qwen-Image-Edit-2511 with Q4 GGUF transformer and 4-bit text encoder")
    gguf_path = hf_hub_download(repo_id=GGUF_REPO, filename=GGUF_FILENAME)
    transformer = QwenImageTransformer2DModel.from_single_file(
        gguf_path,
        config=MODEL_ID, subfolder="transformer",
        quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
        torch_dtype=torch.bfloat16,
    )
    transformer.to("cuda")

    encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID, subfolder="text_encoder",
        torch_dtype=torch.bfloat16, low_cpu_mem_usage=True,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
        ),
        device_map="cuda",
    )
    pipeline = QwenImageEditPlusPipeline.from_pretrained(
        MODEL_ID,
        transformer=transformer,
        text_encoder=encoder,
        torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=True,
    )
    pipeline.vae.to("cuda")
    pipeline.set_progress_bar_config(disable=True)
    if hasattr(pipeline, "enable_vae_tiling"):
        pipeline.enable_vae_tiling()
    log.info("Qwen editing model loaded")
    return pipeline


def render_qwen_image(pipeline, prompt, references, width, height):
    import torch
    params = qwen_inputs(prompt, references, width, height)
    params["generator"] = torch.Generator(device="cuda").manual_seed(secrets.randbits(32))
    with torch.inference_mode():
        return pipeline(**params).images[0]
