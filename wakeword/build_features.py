"""Аугментация клипов и расчёт признаков (спектрограмм) для обучения microWakeWord.

  python build_features.py --workdir D:/wake                      # положительные из generated_samples
  python build_features.py --workdir D:/wake --negatives          # «обманки» из generated_negatives

Аугментации: фоновый шум AudioSet/FMA (SNR −5…10 дБ), реверберация MIT RIR, усиление, лёгкие искажения.
Если какой-то папки с шумом нет (prepare_data.py не запускался), она пропускается.
"""
import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--negatives", action="store_true")
    ap.add_argument("--repetition", type=int, default=2, help="сколько раз повторять обучающий набор с разной аугментацией")
    args = ap.parse_args()
    work = args.workdir

    from microwakeword.audio.augmentation import Augmentation
    from microwakeword.audio.clips import Clips
    from microwakeword.audio.spectrograms import SpectrogramGeneration
    from mmap_ninja.ragged import RaggedMmap

    src = work / ("generated_negatives" if args.negatives else "generated_samples")
    dst = work / ("adversarial_features" if args.negatives else "generated_augmented_features")
    background = [str(work / d) for d in ("fma_16k", "audioset_16k") if (work / d).exists()]
    impulses = [str(work / "mit_rirs")] if (work / "mit_rirs").exists() else []
    print("фоны:", background, "RIR:", impulses)

    clips = Clips(input_directory=str(src), file_pattern="*.wav", max_clip_duration_s=None, remove_silence=False,
                  random_split_seed=10, split_count=0.1)
    augmenter = Augmentation(
        augmentation_duration_s=3.2,
        augmentation_probabilities={"SevenBandParametricEQ": 0.1, "TanhDistortion": 0.1, "PitchShift": 0.1,
                                    "BandStopFilter": 0.1, "AddColorNoise": 0.1,
                                    "AddBackgroundNoise": 0.75 if background else 0.0, "Gain": 1.0,
                                    "RIR": 0.5 if impulses else 0.0},
        impulse_paths=impulses, background_paths=background,
        background_min_snr_db=-5, background_max_snr_db=10, min_jitter_s=0.195, max_jitter_s=0.205)

    dst.mkdir(exist_ok=True)
    for split, split_name, repetition, slide in (("training", "train", args.repetition, 10),
                                                 ("validation", "validation", 1, 10), ("testing", "test", 1, 1)):
        out = dst / split
        out.mkdir(exist_ok=True)
        spectrograms = SpectrogramGeneration(clips=clips, augmenter=augmenter, slide_frames=slide, step_ms=10)
        RaggedMmap.from_generator(out_dir=str(out / "wakeword_mmap"),
                                  sample_generator=spectrograms.spectrogram_generator(split=split_name, repeat=repetition),
                                  batch_size=100, verbose=True)
    print("Признаки сохранены:", dst)


if __name__ == "__main__":
    main()
