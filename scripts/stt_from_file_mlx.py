# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "huggingface_hub",
#     "moshi_mlx",
#     "numpy",
#     "sentencepiece",
#     "sounddevice",
#     "sphn",
# ]
# ///

import argparse
import json

import mlx.core as mx
import mlx.nn as nn
import sentencepiece
import sphn
from huggingface_hub import hf_hub_download
from moshi_mlx import models, utils

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("in_file", help="The file to transcribe.")
    parser.add_argument("--max-steps", default=4096)
    parser.add_argument("--hf-repo", default="kyutai/stt-1b-en_fr-mlx")
    args = parser.parse_args()

    audio, _ = sphn.read(args.in_file, sample_rate=24000)
    lm_config = hf_hub_download(args.hf_repo, "config.json")
    with open(lm_config, "r") as fobj:
        lm_config = json.load(fobj)
    mimi_weights = hf_hub_download(args.hf_repo, lm_config["mimi_name"])
    moshi_name = lm_config.get("moshi_name", "model.safetensors")
    moshi_weights = hf_hub_download(args.hf_repo, moshi_name)
    text_tokenizer = hf_hub_download(args.hf_repo, lm_config["tokenizer_name"])

    lm_config = models.LmConfig.from_config_dict(lm_config)
    model = models.Lm(lm_config)
    model.set_dtype(mx.bfloat16)
    if moshi_weights.endswith(".q4.safetensors"):
        nn.quantize(model, bits=4, group_size=32)
    elif moshi_weights.endswith(".q8.safetensors"):
        nn.quantize(model, bits=8, group_size=64)

    print(f"loading model weights from {moshi_weights}")
    model.load_weights(moshi_weights, strict=True)

    print(f"loading the text tokenizer from {text_tokenizer}")
    text_tokenizer = sentencepiece.SentencePieceProcessor(text_tokenizer)  # type: ignore

    print(f"loading the audio tokenizer {mimi_weights}")
    audio_tokenizer = models.mimi.Mimi(models.mimi_202407(32))
    audio_tokenizer.load_pytorch_weights(str(mimi_weights), strict=True)
    print("warming up the model")
    model.warmup()
    gen = models.LmGen(
        model=model,
        max_steps=args.max_steps,
        text_sampler=utils.Sampler(top_k=25, temp=0),
        audio_sampler=utils.Sampler(top_k=250, temp=0.8),
        check=False,
    )

    print(f"starting inference {audio.shape}")
    audio = mx.concat([mx.array(audio), mx.zeros((1, 48000))], axis=-1)
    for start_idx in range(0, audio.shape[-1] // 1920 * 1920, 1920):
        block = audio[:, None, start_idx:start_idx + 1920]
        other_audio_tokens = audio_tokenizer.encode_step(block)
        other_audio_tokens = mx.array(other_audio_tokens).transpose(0, 2, 1)
        text_token = gen.step(other_audio_tokens[0])
        text_token = text_token[0].item()
        audio_tokens = gen.last_audio_tokens()
        _text = None
        if text_token not in (0, 3):
            _text = text_tokenizer.id_to_piece(text_token)  # type: ignore
            _text = _text.replace("▁", " ")
            print(_text, end="", flush=True)
    print()

