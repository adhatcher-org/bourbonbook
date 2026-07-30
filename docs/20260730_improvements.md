# Improvements

## Batch Catalog Loads

1. When applying catalog batches, instead of loading each individual item one at a time, batch them up into a single json and submit it and allow the app to process the json in the background instead of making multiple API calls, as this is very ineffecient and can result in application latency.
2. There needs to be some type of "processing request" feedback after clicking button. Currently, there is no application feedback to let the user know the batch is being processed.

## Fill Level

Fill level is geometry, not language. Segment the bottle interior and the liquid region, then compute fill as a volume-of-revolution integral over the cross-sectional radius rather than a naive height ratio — bourbon bottles have shoulders and tapers, so height ratio will systematically overestimate. A YOLOv11-seg or SAM2-derived model fine-tuned on 200–400 masked images will run in ~15ms and beat any VLM by a mile. Your real enemies here are amber glass, dark liquid against dark glass, and labels occluding the liquid line — worth adding a capture hint in the app ("shoot against a light background").

## Resolution Issues

The resolution problem is probably your biggest text lever
A phone photo of a whole bottle, downsampled by Ollama's default preprocessing to ~1M pixels, leaves "Aged 12 Years" and barrel/rick/warehouse stamps at a handful of pixels tall. That likely explains most of your name misses and will absolutely kill barrel info. Detect the bottle, crop to the label, upscale, then run text extraction. Do that before you change models — it may be worth more than any swap.

Models to try, in order

1. **Qwen2.5-VL-7B-Instruct** at bf16 (~16GB). Still the reference point for open-weight OCR at this scale, and critically it exposes min_pixels/max_pixels so you control resolution explicitly. A dense 7B often beats a 3B-active MoE on fine print.

2. A dedicated OCR model + a text LLM. PaddleOCR-VL, dots.ocr, or Surya for raw text extraction, then any decent 7–14B text model to structure it. This is frequently the winning architecture for label reading, and it's fast — you'd likely land well under your current 3s p50.
InternVL3.5-8B or -14B as an alternative VLM if you want single-pass.

## Cheap accuracy you're leaving on the table

1. **Proof** = 2 × ABV. Derive one from the other and cross-check. Both fields should equal the max of the two.
2. **Catalog matching**. Don't ask the model to produce an exact product name. Get OCR text, then fuzzy-match (rapidfuzz, or embedding search) against a bourbon catalog. Constrain output to catalog entries. This is where your 52% name accuracy goes to 85%+.
3. **Size** is nearly always one of ~6 values. Snap to the nearest.

## The honest endgame here: this is a narrow domain with consistent visual structure. 

A LoRA on `Qwen2.5-VL-7B` with 500–1000 labeled bottles will outperform every zero-shot model you can fit on that 3090. The pipeline above is what makes that fine-tune cheap, because you're only fine-tuning the text stage on clean crops.
