# IranSpoty logo pack

Every IranSpoty logo, in one place (handed over 2026-10-02). The swirl mark
is a cyan-to-violet gradient (`#00e7ff` → `#693c91`, top to bottom); the
wordmark reads "IranSpoty".

| File | What | Use it on |
|---|---|---|
| `logo-horizontal-dark.svg` | mark left of the wordmark, wordmark `#fff` | dark grounds - the console, the Station |
| `logo-horizontal-light.svg` | mark left of the wordmark, wordmark `#232323` | light grounds |
| `logo-vertical-dark.svg` | mark above the wordmark, wordmark `#fff` | dark grounds, centred blocks (a sign-in card) |
| `logo-vertical-light.svg` | mark above the wordmark, wordmark `#232323` | light grounds, centred blocks |
| `mark.svg` | the swirl alone, no name | icons, favicons, tight spaces |

`original/` holds the files exactly as the designer exported them. The
copies beside it draw the same shapes and are the ones to put on a web
page: the designer's `<style>` block (global `.cls-1`/`.cls-2` rules, which
collide when two logos share a page) is folded into `fill` attributes, and
each gradient has its own id (`isg-<file name>`), so any of them can be
inlined together.

Sizes, from the viewBoxes: horizontal 208.3 x 48.2 (about 4.3 : 1),
vertical 80 x 98.3, mark 80 x 84. In the horizontal logo the wordmark
starts 56.8 units from the left - 1.18 times the logo's height - which is
where a line set under the name (such as "CLOUD FARM") should begin.

In use: `src/geelark_farm/web/static/station_brand.svg` is the horizontal
dark logo with its wordmark on `currentColor`; `BRAND_MARK` in
`src/geelark_farm/web/pages.py` is `mark.svg`.
