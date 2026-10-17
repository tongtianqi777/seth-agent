# Brand kit

The school's logos, colors, fonts, slogan and address, kept as plain files so the agent can use them to make flyers and other material. [brand.json](brand.json) is the index: it says which file is for what, so nothing has to be guessed from a filename.

| Path | What it holds |
|---|---|
| `brand.json` | The manifest: logos and when to use each, colors, fonts, slogan, address, art direction |
| `logos/` | `logo-light.png` (for light backgrounds), `logo-dark.png` (for dark backgrounds), `crest.png` (the shield alone) |
| `fonts/` | Font files, if a flyer ever needs a fixed typeface. Empty for now |
| `motifs/` | The art kit: 16 hand-drawn SVG motifs in the school's palette (bunting, balloons, sun, hills, tree, schoolhouse, flowers, butterflies, crayons, leaves, ribbons, sparkles, squiggle, clouds, books). `brand.json` lists each with its size and what it is for. Designers compose flyers from them |
| `templates/` | Flyer layouts. `event.html` (the default) and `event-collage.html` (photos as tilted prints) are the layouts `seth-agent flyer` fills in with the event's name, date, time and place. Pick one with `--layout` |

## Rules

- Use `logo-light.png` on light backgrounds and `logo-dark.png` on dark ones. Don't recolor, stretch or add effects to a logo.
- **Put the school's slogan on every design**, small and near the bottom, word for word: *Christ-centered education • Unity among family, school, & church • Building Christian character*. It is in `brand.json` under `slogan`; don't retype it.
- **Put the school's address on designs for events held at the school** (1395 Hollenbeck Ave, Sunnyvale, CA 94087, in `brand.json` under `address`). Leave it off when the event is somewhere else or nobody needs to come.
- **Every flyer the agent makes carries the logo, the slogan and a background of artwork**, in the school's colors and font, however it was designed. Photos from the school's library go on it when they are possible and appropriate (never on a closure or a serious notice).
- Colors come from the logo: navy, green, gold and cream. Use navy for headlines and keep green and gold as accents.

## Flyers

Each flyer is designed from scratch by the model, using the logos, colors, slogan, address, art kit and photos from this folder, and held to the rules above. `templates/` holds two fixed layouts for when you want a flyer at once. `seth-agent flyer "..."` reads this folder every time it runs: the logo and colors from `brand.json`, the page from `templates/event.html`. Change a color or swap a logo here and the next flyer uses it. The `art` section of `brand.json` is the art direction for generated artwork (style, palette, composition, what to avoid), and is added to every picture request word for word. Nothing about the brand is written into the code.

## Changing an asset

Replace the file and keep its name, so `brand.json` stays correct. If you add a new asset, add it to `brand.json` with a `use` line saying when to choose it.

The dashboard serves its own copies of the logos from `src/seth_agent/dashboard/`, because it ships inside the Python package. A test checks that they match this folder, so update both when a logo changes.
