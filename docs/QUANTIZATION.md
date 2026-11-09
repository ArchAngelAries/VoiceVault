# Drama 3 sizes (quantization)

Drama 3's official checkpoint is 41.5 GB. VoiceVault converts it on your PC into smaller packages. Smaller packages need less graphics memory and RAM, and they can **lose quality**.

| Size | About | Tested | What to expect |
|---|---|---|---|
| Q2 | 17 GB | no | Smallest. Quality loss is likely: voices can sound flat, less like the reference, or garbled. |
| Q3 | 21 GB | no | For 8–12 GB graphics cards. Some quality loss is likely. |
| **Q4** | **25 GB** | **yes** | **Recommended, always built.** Close to the original in our listening tests. |
| Q5 | 29 GB | no | Between Q4 and Q6. |
| Q6 | 34 GB | yes | Max quality in our tests; slower voice. |
| Q8 | 42 GB | no | Closest to the original; largest and slowest. |

Every size is made with the same proven conversion method: only the bulk "expert" layers change, while the routers and speech head stay at full precision. **Tested** means that size has been built and listened to on the project's test PC (an AMD RX 7900 XT), with the same lines compared against the original by ear and with Whisper. The other sizes haven't been tried on that PC yet, so the app labels them "not yet tested". Sizes below Q4 also say "lower quality", and before building them you have to confirm you understand that.

## How to build sizes

1. Settings → Models → Fish Audio Drama 3 → **Download and convert**, or **Build other sizes** if it's installed.
2. Tick the sizes you want. Q4 is always built.
3. Optional: **Keep the downloaded original** (41.5 GB), so you can build more sizes later without downloading again.

Converting needs about 140 GB of free disk space while it runs (less when the original is kept), 32 GB of RAM or more, and an hour or more of CPU time per run. Temporary files are deleted at the end.

Each size appears as its own model in the model pickers. Its name states the level and, for sizes below Q4, "lower quality", so it's never mistaken for the full model.

VoiceVault never distributes converted files. Every package is made on your PC from Fish Audio's official checkpoint, and the licence notice is written next to it.
