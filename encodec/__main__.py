# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
"""Command-line for audio compression."""

import argparse
from pathlib import Path
import sys
import time

import torchaudio
import torch

from .compress import compress, decompress, MODELS
from .utils import save_audio, convert_audio

SUFFIX = ".ecdc"


def get_parser():
    parser = argparse.ArgumentParser(
        "encodec",
        description="High fidelity neural audio codec. "
        "If input is a .ecdc, decompresses it. "
        "If input is .wav, compresses it. If output is also wav, "
        "do a compression/decompression cycle.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input file, whatever is supported by torchaudio on your system.",
    )
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="Output file, otherwise inferred from input file.",
    )
    parser.add_argument(
        "-b",
        "--bandwidth",
        type=float,
        default=6,
        choices=[1.5, 3.0, 6.0, 12.0, 24.0],
        help="Target bandwidth (1.5, 3, 6, 12 or 24). 1.5 is not supported with --hq.",
    )
    parser.add_argument(
        "-q",
        "--hq",
        action="store_true",
        help="Use HQ stereo model operating on 48 kHz sampled audio.",
    )
    parser.add_argument(
        "-l",
        "--lm",
        action="store_true",
        help="Use a language model to reduce the model size (5x slower though).",
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="Overwrite output file if it exists."
    )
    parser.add_argument(
        "-s",
        "--decompress_suffix",
        type=str,
        default="_decompressed",
        help="Suffix for the decompressed output file (if no output path specified)",
    )
    parser.add_argument(
        "-r",
        "--rescale",
        action="store_true",
        help="Automatically rescale the output to avoid clipping.",
    )
    parser.add_argument(
        "-g", "--gpu", action="store_true", help="Use GPU if available."
    )
    parser.add_argument(
        "-t", "--time", action="store_true", help="Print elapse time information."
    )
    return parser


def fatal(*args):
    print(*args, file=sys.stderr)
    sys.exit(1)


def check_output_exists(args):
    if not args.output.parent.exists():
        fatal(f"Output folder for {args.output} does not exist.")
    if args.output.exists() and not args.force:
        fatal(f"Output file {args.output} exist. Use -f / --force to overwrite.")


def check_clipping(wav, args):
    if args.rescale:
        return
    mx = wav.abs().max()
    limit = 0.99
    if mx > limit:
        print(
            f"Clipping!! max scale {mx}, limit is {limit}. "
            "To avoid clipping, use the `-r` option to rescale the output.",
            file=sys.stderr,
        )


def main():
    args = get_parser().parse_args()
    # modified by mimbres
    if args.time:
        start = time.time()

    if not args.input.exists():
        fatal(f"Input file {args.input} does not exist.")

    # modified by mimbres
    if args.gpu:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = "cpu"

    if args.input.suffix.lower() == SUFFIX:
        # Decompression
        if args.output is None:
            args.output = args.input.with_name(
                args.input.stem + args.decompress_suffix
            ).with_suffix(".wav")
        elif args.output.suffix.lower() != ".wav":
            fatal("Output extension must be .wav")
        check_output_exists(args)
        out, out_sample_rate = decompress(args.input.read_bytes(), device=device)
        check_clipping(out, args)
        if args.gpu:
            out = out.cpu()
        save_audio(out, args.output, out_sample_rate, rescale=args.rescale)
    else:
        # Compression
        if args.output is None:
            args.output = args.input.with_suffix(SUFFIX)
        elif args.output.suffix.lower() not in [SUFFIX, ".wav", ".pt"]:
            fatal(f"Output extension must be .wav, .pt or {SUFFIX}")
        check_output_exists(args)

        model_name = "encodec_48khz" if args.hq else "encodec_24khz"
        model = MODELS[model_name]()
        if args.bandwidth not in model.target_bandwidths:
            fatal(
                f"Bandwidth {args.bandwidth} is not supported by the model {model_name}"
            )
        model.set_target_bandwidth(args.bandwidth)

        wav, sr = torchaudio.load(args.input)
        wav = convert_audio(wav, sr, model.sample_rate, model.channels)
        compressed = compress(model, wav, use_lm=args.lm, device=device)
        if args.output.suffix.lower() == SUFFIX:
            args.output.write_bytes(compressed)
        # modified by platers
        elif args.output.suffix.lower() == ".pt":
            # Save embedding only
            emb = model.encoder(wav.unsqueeze(0).to(device))
            emb = emb.squeeze(0).cpu()
            torch.save(emb, args.output)
        else:
            # Directly run decompression stage
            assert args.output.suffix.lower() == ".wav"
            out, out_sample_rate = decompress(compressed, device=device)
            check_clipping(out, args)
            if args.gpu:
                out = out.cpu()
            save_audio(out, args.output, out_sample_rate, rescale=args.rescale)

    # modified by mimbres
    if args.time:
        end = time.time()
        print(
            f"Time elapsed: {end - start} seconds for b={args.bandwidth}, hq={args.hq}, lm={args.lm}, device={device}"
        )


if __name__ == "__main__":
    main()
