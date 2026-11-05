# Training a local voice

Updated 2026-10-10. Dataset preparation, managed jobs, cancellation and explicit model publication are implemented. Training is experimental on Windows and AMD. The workflow has passed focused CPU and synthetic browser checks; actual GPU training and resulting voice quality remain unverified.

## Prepare and review

1. Save clean reference clips in Harvest.
2. Open Train → Dataset review. Name the dataset, optionally identify its speaker and language, select references, and create it.
3. Listen to each copied clip. Correct its transcript, then mark it Reviewed by me. Exclude unsuitable clips.
4. Save the review. Every included clip needs a valid transcript and review before export or training.

Original Harvest references stay unchanged. Editing a transcript clears its review flag. Switching between Dataset review and Training runs preserves the current draft; save it before starting a run.

Audio measurements can flag clipping, very quiet audio and format problems. They do not establish pronunciation, speaker likeness or sound quality. Download ZIP creates a portable dataset with relative audio paths and a manifest; it does not train a model.

## Run a compatibility check

Open Training runs and choose an installed OmniVoice model. Use Run one-step check first. This runs the actual local preprocessor and trainer and writes a separate checkpoint; it can use substantial GPU memory and disk space even though it performs only one training step.

Longer training becomes available only after that model passes a real one-step check with the current model files and runtime. Installed dependencies and a synthetic test result cannot unlock it. Other speech engines remain available for their supported generation features; this training workflow currently supports OmniVoice.

The trainer uses local model files and the isolated training dependencies. It does not download replacement model weights. If preprocessing rejects a clip, trim or correct it in Harvest and prepare a reviewed dataset again.

## Monitor and cancel

Recent runs retain their status and a bounded log. Request cancel stops the owned training work; canceled and failed runs are not completed models. An interrupted run is recorded after restart. Training shares the speech GPU lane, so speech requests may need to wait.

Normal runs retain their latest two checkpoints. Source model files are preserved. Existing failed or partial job artifacts are retained for diagnosis; cancellation does not automatically reclaim all disk space.

## Add a completed voice to Generate

Use Add to Generate on a completed run. This explicitly copies the inference files into the app-owned model library, using additional disk space. The original checkpoint stays in place. Publication is atomic, retries reuse a matching verified copy, and canceled jobs cannot be published.

Generate discovers published models without adding personal paths to settings. If its model list could not refresh, rescan models in Generate. Publication makes the model available for use; it does not mark inference or voice quality as tested.

Listen to a short generated sample before relying on a trained voice. A one-step checkpoint is principally a compatibility check, rather than evidence of a successfully learned character voice.

Training is experimental: turn it on in Settings → General → Experimental features.
