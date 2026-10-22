# Frontend Design Playbook (English)

Complete methodology for producing distinctive, production-grade frontend interfaces. Load this
before writing any code. The Chinese counterpart is `references/zh-CN.md`; both carry identical
rules, benchmarks and checklists.

---

## 1. Design Thinking Protocol

Answer four questions in writing before the first line of code:

| Question | Output |
|---|---|
| **Purpose** | One sentence: what problem this interface solves, for whom |
| **Tone** | One extreme direction from the list below, never a blend |
| **Constraints** | Framework, performance budget, accessibility target, browser support |
| **Differentiation** | The single thing a visitor will remember |

**Tone menu** — pick exactly one:

| Direction | Signature moves | Watch out for |
|---|---|---|
| Brutally minimal | Extreme whitespace, one accent, precise spacing | Ending up empty instead of refined |
| Maximalist chaos | Layering, mixed type, clashing color, density | Becoming noise instead of richness |
| Retro-futuristic | CRT scanlines, neon glow, monospace, grid horizons | Gimmick overriding usability |
| Organic / natural | Soft curves, earth tones, paper grain, botanical forms | Looking unfinished |
| Luxury / refined | Serif display, restrained metallic accent, large margins | Sliding into generic corporate |
| Editorial / magazine | Multi-column grid, rules, drop caps, pull quotes | Breaking on small screens |
| Brutalist / raw | Exposed borders, harsh black lines, system-like controls | Failing contrast or readability |
| Playful / toy-like | Rounded forms, bright blocks, springy motion | Reading as childish in B2B contexts |
| Industrial / utilitarian | Dashboard density, tabular numerals, hairline borders | Losing hierarchy in the density |

**Rule**: A clear conceptual direction executed with precision beats any amount of decoration.
Bold maximalism and refined minimalism both work — intentionality is the variable, not intensity.

---

## 2. Typography System

Typography is the fastest differentiator available. A font swap changes perceived quality more than
any other single decision.

### Banned as display faces

Inter, Roboto, Arial, Helvetica, system-ui, Open Sans, Lato, Montserrat, Poppins, Space Grotesk.
These are correct choices for generic output — which is exactly the problem.

### Pairing pattern

One distinctive **display** face + one refined **body** face. Never two loud faces.

| Direction | Display | Body |
|---|---|---|
| Luxury / editorial | Playfair Display, Bodoni Moda, Cormorant Garamond | Source Serif 4, Lora |
| Brutalist / industrial | Archivo Black, Anton, Bebas Neue | IBM Plex Sans, Public Sans |
| Retro-futuristic | Orbitron, Chakra Petch, Monoton | IBM Plex Mono, JetBrains Mono |
| Organic / natural | Fraunces, Bricolage Grotesque, Newsreader | Karla, Work Sans |
| Playful | Fredoka, Baloo 2, Rubik Bubbles | Nunito, Quicksand |
| Technical / dashboard | Space Mono, JetBrains Mono, IBM Plex Mono | Inter Tight, Söhne |

### Rules

- Load fonts deliberately: `preconnect` + `font-display: swap`, subset to the weights actually used.
- Display sizes: hero ≥ 48px, ideally 64–120px on desktop; tracking −0.02em to −0.04em.
- Body line length 45–75 characters (Latin), line-height 1.5–1.7; headings 0.95–1.1.
- Establish a modular type scale (e.g. 1.25 ratio) as CSS variables; never ad-hoc values.
- Use tabular numerals (`font-variant-numeric: tabular-nums`) in data-dense UI.
- CJK pages: pair a Chinese face (Noto Serif SC / Source Han Sans / LXGW WenKai) with the Latin
  display face; set `line-height` ≥ 1.7 and letter-spacing 0.02em for body copy.

---

## 3. Color and Theme

| Rule | Detail |
|---|---|
| Commit to a theme | Choose light or dark deliberately; do not default to white |
| Dominant + accent | One dominant surface color, one sharp accent; 60 / 30 / 10 distribution |
| Variables only | Every color as a CSS variable; no hardcoded hex in components |
| Contrast | Body text ≥ 4.5:1, large text ≥ 3:1, UI components ≥ 3:1 |
| Semantic aliases | `--color-bg`, `--color-surface`, `--color-text`, `--color-muted`, `--color-accent` |
| Dark mode | Token swap via `[data-theme="dark"]`; no component rewrites |
| Accent discipline | One accent per screen, reserved for the primary action |

### Never

- Purple gradient on a white background.
- Evenly distributed, timid pastel palettes with no dominant color.
- Pure `#000` text on pure `#fff` at full saturation — use warm off-black and off-white.
- Color as the only signal for state, error or meaning.

### Starter token block

```css
:root {
  --color-bg: #0e0d0c;
  --color-surface: #171614;
  --color-text: #f5f1ea;
  --color-muted: #a09a90;
  --color-accent: #ff6b2c;
  --color-border: rgba(245, 241, 234, 0.12);
  --radius-sm: 4px; --radius-md: 10px; --radius-lg: 20px;
  --space-1: 4px; --space-2: 8px; --space-3: 16px; --space-4: 24px;
  --space-5: 40px; --space-6: 64px; --space-7: 96px;
  --shadow-1: 0 2px 8px rgba(0,0,0,.24);
  --shadow-2: 0 8px 28px rgba(0,0,0,.32);
  --shadow-3: 0 24px 64px rgba(0,0,0,.42);
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-fast: 160ms; --dur-base: 320ms; --dur-slow: 640ms;
}
```

---

## 4. Motion Design

| Principle | Practice |
|---|---|
| CSS first | HTML/CSS deliverables animate with CSS only — no framework dependency |
| React | Use the Motion library when available; otherwise CSS transitions and Web Animations API |
| One orchestrated moment | A single page-load sequence with staggered `animation-delay` delights more than scattered micro-interactions |
| Scroll reveals | Trigger with `IntersectionObserver`; animate `opacity` + `translateY` only |
| Hover | Surprise: scale, letter-spacing shift, underline sweep, background wipe — not just brightness |
| Performance | Animate `transform` and `opacity` exclusively; avoid layout-triggering properties |
| Reduced motion | Wrap every animation in `@media (prefers-reduced-motion: no-preference)` or disable under `reduce` |

### Timing baseline

| Type | Duration | Easing |
|---|---|---|
| Micro-interaction (hover, press) | 150–200ms | `ease-out` |
| Standard transition (menu, panel) | 300–400ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| Scene transition (page, modal) | 600–800ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| Load sequence stagger | 60–120ms between groups, 3–6 groups | — |

### Load sequence skeleton

```css
.reveal { opacity: 0; transform: translateY(24px); }
.reveal.in { opacity: 1; transform: none;
  transition: opacity var(--dur-base) var(--ease-out),
              transform var(--dur-base) var(--ease-out); }
.reveal:nth-child(2) { transition-delay: 80ms; }
.reveal:nth-child(3) { transition-delay: 160ms; }
.reveal:nth-child(4) { transition-delay: 240ms; }
@media (prefers-reduced-motion: reduce) {
  .reveal { opacity: 1; transform: none; transition: none; }
}
```

---

## 5. Spatial Composition

- **Asymmetry over centering.** Offset the hero, break the grid, let one element run off-canvas.
- **Overlap.** Layer type over image, badge over card edge, number over section boundary.
- **Diagonal flow.** Rotate a marquee, slant a divider, stagger card offsets vertically.
- **Grid-breaking.** One element deliberately escapes the column system each screen.
- **Negative space or controlled density.** Choose one; the failure mode is medium everything.
- **Vertical rhythm.** Spacing scale as variables (4 / 8 / 16 / 24 / 40 / 64 / 96px).
- **Responsive intent.** Composition decisions must have a mobile answer, not just a collapse.

---

## 6. Backgrounds and Visual Details

Build atmosphere instead of flat fills. Pick 2–3 techniques that fit the direction:

| Technique | Recipe |
|---|---|
| Gradient mesh | 2–3 large radial gradients with blur, low opacity, on the background layer |
| Noise / grain | SVG `feTurbulence` as a data-URI overlay at 3–6% opacity, `mix-blend-mode: overlay` |
| Geometric pattern | Repeating `linear-gradient` or `conic-gradient` at low opacity |
| Layered transparency | Stacked `backdrop-filter: blur()` panels over a textured base |
| Dramatic shadow | Three-level elevation system (Y 2 / 8 / 24px) with colored, not black, shadows |
| Decorative border | Hairline rules, corner brackets, double strokes, dashed dividers |
| Custom cursor | Small follower dot with blend mode; never on touch-only contexts |
| Marquee | Infinite scrolling band for logos, keywords or ticker data |

```css
.atmosphere::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(60rem 40rem at 12% -10%, rgba(255,107,44,.28), transparent 60%),
    radial-gradient(50rem 40rem at 100% 0%, rgba(94,92,230,.22), transparent 55%);
  filter: blur(0.5px);
}
.grain::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 1; opacity: .05;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}
```

---

## 7. Anti-AI-Slop Checklist

Run before every delivery. Any hit must be fixed or explicitly justified.

| # | Check | Fail signal |
|---|---|---|
| 1 | Display font | Inter / Roboto / Arial / system-ui / Space Grotesk as the display face |
| 2 | Palette | Purple or blue gradient on white; evenly distributed pastels |
| 3 | Layout | Hero = centered heading + subheading + two buttons + three feature cards |
| 4 | Cards | Uniform rounded cards with identical shadow and centered icon |
| 5 | Icons | Generic line-icon set used decoratively with no meaning |
| 6 | Copy | "Seamless", "Empower", "Next-generation", "Unlock the power of" |
| 7 | Motion | Everything fades up with the same 300ms delay, or nothing moves at all |
| 8 | Depth | Flat solid background, zero texture or atmosphere |
| 9 | Personality | Swapping the logo would make it any other product |
| 10 | States | Hover, focus, disabled, empty, error or loading states missing |
| 11 | Differentiation | No single element a visitor could describe from memory |
| 12 | Accessibility | Contrast below 4.5:1, invisible focus ring, no reduced-motion path |

---

## 8. Complexity Matching

| Vision | What the code must contain |
|---|---|
| Maximalist | Layered backgrounds, multiple type treatments, scroll choreography, custom cursors, marquees, dense state coverage |
| Refined minimal | Few elements, but exact spacing, one perfect type pairing, subtle transitions, immaculate alignment |
| Editorial | Multi-column grid, rules, drop caps, pull quotes, image-anchored layout |
| Industrial | Dense information, tabular numerals, hairline borders, status color system |

Elegance comes from executing the vision well — not from the quantity of effects. Restraint is a
skill. Over-decoration on a minimal vision is as wrong as under-delivery on a maximal one.

---

## 9. Technical Baseline

| Area | Requirement |
|---|---|
| Semantics | Real landmarks and headings (`header` / `main` / `section` / `footer`, one `h1`) |
| Keyboard | Everything reachable and operable; visible `:focus-visible` ring (2px + 2px offset) |
| Contrast | Body ≥ 4.5:1, large text ≥ 3:1, UI components ≥ 3:1 |
| Targets | ≥ 44×44px interactive area |
| Responsive | Mobile-first; composition decisions have an explicit mobile answer |
| Reduced motion | All animation disabled under `prefers-reduced-motion: reduce` |
| Performance | Animate `transform` / `opacity`; `font-display: swap`; lazy-load non-critical media |
| State coverage | Default, hover, active, focus-visible, disabled, loading, error, empty |
| Code quality | No placeholders, no "TODO", no dead CSS; comments explain intent |

---

## 10. Delivery Structure

```
<project>/
├── index.html           # structure, semantic landmarks, content
├── styles/
│   ├── tokens.css       # CSS variables: color, type, space, radius, shadow, motion
│   ├── base.css         # reset, typography, focus and reduced-motion rules
│   └── components.css   # component + composition styles
├── scripts/
│   └── main.js          # IntersectionObserver reveals, marquee, micro-interactions
└── README.md            # direction note, how to run, token map, QA result
```

React / Vue projects: mirror the same split — tokens, primitives, composed sections, motion hooks.

---

## 11. Delivery QA Sheet

| Gate | Pass condition |
|---|---|
| Direction stated in one paragraph | Yes / No |
| Display + body font named and loaded | Both present, no banned face |
| Contrast measured, not guessed | Every text pair checked |
| One orchestrated load sequence | 3–6 groups, 60–120ms stagger |
| Reduced-motion path verified | Yes |
| Atmosphere layer present | ≥ 1 texture / gradient / shadow system |
| All component states covered | 8 states where applicable |
| Responsive check at 375 / 768 / 1440px | No overflow, no broken composition |
| Anti-slop checklist run | 0 unresolved hits |
| Code runs as delivered | Opens and works with no missing assets |

Ship working code and a short direction note. Never deliver a description of what could be built.
