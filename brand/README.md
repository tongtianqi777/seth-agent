# Brand kit

The school's logos, colors and fonts, kept as plain files so the agent can use them to make flyers and other material. [brand.json](brand.json) is the index: it says which file is for what, so nothing has to be guessed from a filename.

| Path | What it holds |
|---|---|
| `brand.json` | The manifest: logos and when to use each, colors, fonts |
| `logos/` | `logo-light.png` (for light backgrounds), `logo-dark.png` (for dark backgrounds), `crest.png` (the shield alone) |
| `fonts/` | Font files, if a flyer ever needs a fixed typeface. Empty for now |
| `templates/` | Flyer layouts. Empty for now |

## Rules

- Use `logo-light.png` on light backgrounds and `logo-dark.png` on dark ones. Don't recolor, stretch or add effects to a logo.
- Colors come from the logo: navy, green, gold and cream. Use navy for headlines and keep green and gold as accents.

## Changing an asset

Replace the file and keep its name, so `brand.json` stays correct. If you add a new asset, add it to `brand.json` with a `use` line saying when to choose it.

The dashboard serves its own copies of the logos from `src/seth_agent/dashboard/`, because it ships inside the Python package. A test checks that they match this folder, so update both when a logo changes.
