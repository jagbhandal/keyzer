# Screenshots

These are generated, not hand-captured. Regenerate the full set (and a tour GIF)
with no Razer hardware attached:

```bash
python3 tools/make_screenshots.py --gif
```

It runs KEYZER in demo mode (`KEYZER_DEMO=1`) offscreen and renders a curated
tour to `docs/screenshots/*.png` plus `tour.gif` (the GIF needs ImageMagick's
`convert` on PATH). The generated images are git-ignored; commit the final set
deliberately when refreshing the README so the repo carries the current look.
