# Design-system provenance

Parts of this directory are adapted from **OpenIntelligentUI**
(<https://github.com/CopilotKit/OpenIntelligentUI>), revision
`8391a94c1c3939c679e40ddd7bfc08a9ef690e70` (main on 2026-10-27, verified with
`gh api repos/CopilotKit/OpenIntelligentUI/commits/main`).

```
The MIT License

Copyright (c) Atai Barkai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

| Aegis file | Upstream source (at the revision above) | What was taken | What changed |
| --- | --- | --- | --- |
| `tokens.css` | `apps/app/src/app/globals.css` (Brand Design Tokens, `:root.dark`, spacing, radius, shadow, chart tokens); `packages/design-system/src/index.ts` `THEME_CSS` | lilac/mint brand values, surface/text/border structure, 8 px spacing scale, radii, semantic info/danger/success/warning ramps, chart tooltip/axis tokens, Plus Jakarta Sans + Spline Sans Mono families | dark theme is the default, light is `[data-theme=light]`; glass tokens resolved near-opaque; text colours lifted for contrast; fonts bundled with @fontsource (no Google Fonts request) |
| `base.css` | `index.ts` `FORM_STYLES_CSS`; `apps/app/src/app/chat.css` | box model, body type, form-control treatment, checkbox accent, `fadeSlideIn`/`shimmer`/`spin` keyframes, reduced-motion block, `:focus-visible` ring, skip link | 1 px borders, mint focus ring, no stagger animation on live values, transitions also disabled under reduced motion |
| `components.css` | `globals.css` `.brand-glass-container`, `.glass-card`, `.cta-btn--primary`, `.cta-banner`, `.explainer-card`, `.demo-gallery-drawer`; `chat.css` `.answer-table`; `components/demo-gallery/category-filter.tsx`, `demo-card.tsx`; `index.ts` `SVG_CLASSES_CSS` | panel surface, gradient primary button, banner wash, note card, drawer geometry (bottom sheet < 640 px, right panel ≥ 640 px), table styling, chip row and gradient active chip, tinted category pills, `.c-*` SVG ramps | **no `backdrop-filter`** (panels can overlay live video); no animated blur blobs (`.abstract-bg` not adopted); hover lift removed; semantic tones from THEME_CSS |
| `components.tsx` | `components/demo-gallery/index.tsx`, `category-filter.tsx` | drawer behaviour (Escape, scrim click), single-select chip group | focus trap + focus return, `aria-pressed`, portal, typed props |
| `chart.ts` | `components/generative-ui/charts/config.ts`, `bar-chart.tsx` | `CHART_CONFIG.tooltipStyle` reading CSS variables, axis stroke/tick styling | 3-colour restrained palette; animation off; nulls never connected |

Not adopted (and why): the CopilotKit runtime, chat, generative-UI renderer, sandboxed iframe,
CDN import map (`esm.sh`), OpenAI/Jev model routing, PostHog analytics and the launch
telemetry (`docs/telemetry.md`). Aegis measurements come only from the FastAPI/WebSocket
state; there is no assistant, no model key, and no external request at runtime.
