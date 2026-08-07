from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StylePack:
    slug: str
    name: str
    description: str
    when_to_use: tuple[str, ...]
    spec: str


LIQUID_GLASS = StylePack(
    slug="liquid_glass",
    name="Liquid Glass",
    description="iOS-style frosted glass panels over an ambient, mouse-reactive gradient background. Calm, premium, consumer-facing.",
    when_to_use=("consumer", "landing", "marketing", "portfolio", "premium", "apple", "minimal"),
    spec=(
        "**Typography:** `-apple-system, BlinkMacSystemFont, \"SF Pro Display\", \"SF Pro Text\", system-ui, Helvetica, Arial, sans-serif` "
        "for everything. No separate display font — weight differentiates headings (700) from body/emphasis (500/600).\n\n"
        "**Accent color:** one primary accent (pick a single saturated hue appropriate to the brand, e.g. `#6ea8fe` soft blue), used for "
        "eyebrow dots, links, primary buttons, badges, glow accents — implement as a single theme variable. An optional secondary accent "
        "(a violet or teal a few degrees around the color wheel) may appear only in gradients/blobs alongside the primary.\n\n"
        "**Two themes, toggle at runtime (dark is default):**\n"
        "Dark: page background near-black (`#08080b`); text primary `#f5f5f7`, secondary `rgba(245,245,247,0.68)`, tertiary "
        "`rgba(245,245,247,0.5)`; glass fill `rgba(255,255,255,0.055)` (cards) / `rgba(255,255,255,0.09)` (nav); glass border "
        "`rgba(255,255,255,0.14)` normal / `0.22` strong; glass shadow `0 20px 50px rgba(0,0,0,0.55)` plus an inset top hairline "
        "`inset 0 1px 0 rgba(255,255,255,0.16)`.\n"
        "Light: page background off-white (`#f5f4f1`); text primary `#17181f`, secondary `rgba(23,24,31,0.68)`; glass fill "
        "`rgba(255,255,255,0.5)` / `0.65` strong; glass border `rgba(255,255,255,0.7)`; glass shadow `0 8px 32px rgba(31,41,74,0.1)` plus "
        "inset top hairline `rgba(255,255,255,0.8)`.\n\n"
        "**Glass panel recipe** (cards, nav, buttons, inputs), reused everywhere as a single class/mixin: `backdrop-filter: blur(28-30px) "
        "saturate(180%)` (smaller chips can use `blur(20px)`), background = theme glass fill, `border: 1px solid` theme glass border, "
        "`border-radius: 20px` (cards) / `999px` (pills/nav), box-shadow = theme glass shadow + inset top hairline.\n\n"
        "**Buttons:** Primary = solid accent background, near-black text, pill shape, `box-shadow: 0 8px 24px {accent}55`. Ghost = glass "
        "panel recipe, theme text color, pill shape.\n\n"
        "**Motion:** a soft radial cursor-follow glow (350-450px, accent color, `mix-blend-mode: plus-lighter` dark / `multiply` light) "
        "behind all content — mutate its position via direct DOM ref on `mousemove`, never React state (avoids re-render lag). 2-3 large "
        "(500-700px), heavily blurred (80-100px), low-opacity ambient color blobs drift slowly (24-32s ease-in-out infinite) behind "
        "content, offset so they never sync. Any floating decorative shapes get their own slow independent float keyframe "
        "(translateY + slight rotate, 5-7s).\n\n"
        "**Scale:** border radius 20px (cards) / 24px (larger floating shapes) / 999px (pills); max content width ~1120px, page "
        "horizontal padding 24px; card padding 24-28px; grid gaps 16-20px."
    ),
)

NEUBRUTALISM = StylePack(
    slug="neubrutalism",
    name="Neubrutalism",
    description="Flat saturated color blocks, thick black borders, hard offset shadows, no gradients or blur. Loud, confident, internet-native.",
    when_to_use=("bold", "playful", "startup", "creative", "youth", "brutalist", "loud", "meme"),
    spec=(
        "**Typography:** a single grotesque/geometric sans at extreme weight contrast — headings 800-900 weight, very large "
        "(clamp(32px, 6vw, 72px)), tight letter-spacing (-0.02em); body 500-600 weight, 16-18px. No serif, no script, no soft rounded "
        "display font.\n\n"
        "**Color:** flat, fully opaque, high-saturation blocks — pick 3-4 colors from a punchy set (e.g. `#FFE347` yellow, `#FF5C5C` "
        "coral, `#6BCB77` green, `#4D96FF` blue) plus pure `#000000` and pure `#FFFFFF`. No gradients, no transparency, no blur anywhere. "
        "Background is a single flat color, never a photo or gradient.\n\n"
        "**Borders and shadows:** every interactive element (card, button, input, nav) gets a solid `3-4px solid #000` border with SHARP "
        "corners or a small fixed radius (8-12px, same value everywhere, never pill-shaped). Shadows are hard offset blocks, not blur: "
        "`box-shadow: 6px 6px 0 #000` (or 8px 8px for larger cards) — no blur radius. On hover/press, shift the element by "
        "translate(2px, 2px) and shrink the shadow offset to 2px 2px to read as a physical push.\n\n"
        "**Buttons:** solid color fill, `3px solid #000` border, `4px 4px 0 #000` shadow, bold uppercase label, small fixed radius. "
        "Press state: `translate(2px, 2px)` + shadow shrinks to `2px 2px 0 #000`.\n\n"
        "**Layout:** asymmetric, slightly rotated elements (`transform: rotate(-1deg to 2deg)`) breaking a strict grid deliberately; "
        "generous negative space around a few oversized elements rather than many small ones. Decorative shapes are flat solid geometric "
        "primitives (circles, thick zigzag lines, stars) with the same hard border/shadow treatment — never soft blobs or photos.\n\n"
        "**Motion:** minimal and snappy — no slow ambient drifting. Hover/press transitions are 80-120ms linear or ease-out, no bounce. "
        "A occasional single elements may have a small continuous wiggle/rotate loop (2-3s) for personality, used sparingly (1-2 per page)."
    ),
)

SWISS_EDITORIAL = StylePack(
    slug="swiss_editorial",
    name="Swiss Editorial",
    description="Strict grid, huge confident type, near-monochrome palette with one accent rule. Serious, high-trust, content-first.",
    when_to_use=("professional", "editorial", "news", "publication", "enterprise", "b2b", "finance", "legal", "corporate"),
    spec=(
        "**Typography:** one grotesque sans (Helvetica/Inter/Suisse-like) for everything. Headings set extremely large "
        "(clamp(40px, 6vw, 96px)), weight 700, tight leading (1.0-1.05), tight tracking (-0.03em). Body copy 16-18px, weight 400-450, "
        "generous line-height (1.6). Never more than two weights on screen at once.\n\n"
        "**Color:** near-monochrome — pure or near-black text (`#111`) on pure or near-white background (`#fafafa`), a single dark mode "
        "inversion (`#0d0d0d` background, `#f2f2f2` text). Exactly ONE accent color (a single saturated red, orange, or blue) used only "
        "for: the underline/rule under section labels, one CTA button, and link hover states — never for decoration or backgrounds.\n\n"
        "**Grid:** a strict visible or implied 12-column grid, generous outer margins (48-96px on desktop), content strictly aligned to "
        "column edges — nothing centered-and-floating. Section labels are small (11-12px), uppercase, letter-spaced (0.1em), often paired "
        "with a thin horizontal rule and a running number (\"01\", \"02\").\n\n"
        "**Cards/panels:** no glass, no shadow, no gradient. A hairline `1px solid` border in a light gray (`rgba(0,0,0,0.12)`) or no "
        "border at all, relying purely on whitespace and a horizontal rule to separate sections. Images are full-bleed or strictly "
        "grid-aligned, always with a thin caption in the label typography.\n\n"
        "**Buttons:** rectangular or very slightly rounded (4-6px), border-only (no fill) by default, filled with the single accent color "
        "only for the primary CTA. Text label, no icon-heavy decoration.\n\n"
        "**Motion:** restrained — a rule/line draws in on scroll, text fades up 8-12px on entry, numbers count up once. No ambient "
        "background motion, no floating shapes, no blur. Hover states are instant color/underline changes, not transforms."
    ),
)

CYBERPUNK_NEON = StylePack(
    slug="cyberpunk_neon",
    name="Cyberpunk Neon",
    description="Near-black backgrounds, saturated neon glow accents, scanline/glitch texture. High-energy, tech/gaming.",
    when_to_use=("gaming", "tech", "crypto", "hacker", "futuristic", "neon", "dark", "cyberpunk", "esports"),
    spec=(
        "**Typography:** a technical/monospace or condensed grotesque for headings (weight 700-800, slightly wide letter-spacing on "
        "small labels, tight on large display text); a clean readable sans for body copy so long-form text stays legible against the "
        "dark background.\n\n"
        "**Color:** page background near-black (`#0a0a0f` to `#050507`), never pure black (crushes the glow effect). Two neon accents "
        "from opposite ends of the spectrum for contrast, e.g. cyan `#00f0ff` and magenta `#ff2ee6`, occasionally a third acid green "
        "`#b6ff3c` for alerts/success. Text primary near-white with a faint cool tint (`#e8f4ff`); secondary a muted blue-gray "
        "(`rgba(200,220,255,0.55)`).\n\n"
        "**Glow recipe:** neon elements (borders, key text, icons, active states) use layered `box-shadow`/`text-shadow` glow: "
        "`0 0 8px {accent}, 0 0 24px {accent}66, 0 0 48px {accent}33` — stack 2-3 blur radii, never a single soft shadow. Card borders are "
        "1-1.5px solid at 40-60% opacity of the accent with the same glow applied on hover/focus, not by default (default state is "
        "dimmer, hover \"powers on\").\n\n"
        "**Texture:** a subtle repeating scanline overlay (thin horizontal lines, 2-4% opacity, `pointer-events:none`, fixed over the "
        "whole viewport) and/or a faint grid/circuit-line background pattern in a dim accent tone. Occasional chromatic-aberration-style "
        "double image on large display headings (two offset colored text layers, 1-2px offset) used sparingly on hero text only.\n\n"
        "**Panels:** dark glass — `background: rgba(15,15,25,0.6)`, `backdrop-filter: blur(12px)`, thin glowing border as above, sharp or "
        "barely-rounded corners (4-8px) to read as HUD/interface rather than soft consumer UI.\n\n"
        "**Motion:** glow pulses slowly (2-3s ease-in-out infinite) on key active elements; a thin scan-line sweep animation "
        "occasionally crosses a hero panel; hover states snap on quickly (100-150ms) rather than easing softly, reinforcing an "
        "electronic/digital feel."
    ),
)

SOFT_NEUMORPHISM = StylePack(
    slug="soft_neumorphism",
    name="Soft Neumorphism",
    description="Monochrome extruded surfaces with soft dual shadows, as if pressed from the same material as the background. Calm, tactile.",
    when_to_use=("wellness", "calm", "app", "minimal", "soft", "meditation", "health", "tactile"),
    spec=(
        "**Typography:** a single rounded or humanist sans (soft terminals, medium contrast) — headings weight 600-700, body 400-500. "
        "Generous line-height throughout; avoid anything condensed or sharp-edged.\n\n"
        "**Color:** near-monochrome — background and surfaces are the SAME base color (a soft off-white `#e9eef2` or, for dark mode, a "
        "soft dark gray-blue `#1e2228`), with all depth coming from shadow, not color contrast. One muted accent (a soft blue, sage, or "
        "lavender) used sparingly for the primary action and active states only.\n\n"
        "**Extrusion recipe (the core technique):** every raised surface uses a DUAL shadow — one soft dark shadow bottom-right, one soft "
        "light highlight top-left, both large and low-opacity: `box-shadow: 8px 8px 16px rgba(0,0,0,0.12), -8px -8px 16px "
        "rgba(255,255,255,0.7)` (invert the light shadow's color/opacity for dark mode). No border. Corners are generously rounded "
        "(16-24px). Pressed/inset elements (active buttons, input fields) INVERT the technique: `inset 4px 4px 8px rgba(0,0,0,0.12), "
        "inset -4px -4px 8px rgba(255,255,255,0.7)` so they read as pressed into the surface rather than raised.\n\n"
        "**Buttons:** same-color-as-background raised surface (per the recipe above) with a subtle accent-tinted icon or label; on "
        "press, transition instantly to the inset/pressed shadow variant.\n\n"
        "**Icons:** simple, single-weight line icons or soft filled shapes in the muted accent — never multi-color or detailed "
        "illustration; icons should look like they're carved from the same soft material.\n\n"
        "**Motion:** very gentle — a slight shadow-depth increase on hover (200ms ease), press transitions are instant (extruded → "
        "inset). No ambient background motion, no glow, no blur-heavy glass. The whole interface should feel still and calm except for "
        "direct interaction feedback."
    ),
)

CORPORATE_GRADIENT_MESH = StylePack(
    slug="corporate_gradient_mesh",
    name="Corporate Gradient Mesh",
    description="Clean white SaaS surfaces with soft multi-color gradient-mesh blobs as the only decoration. Trustworthy, modern B2B.",
    when_to_use=("saas", "b2b", "product", "software", "startup", "dashboard", "modern", "clean"),
    spec=(
        "**Typography:** a clean geometric or grotesque sans (Inter/Manrope-like) — headings weight 600-700 with tight tracking, body "
        "400-450 weight at 15-17px. Numbers/metrics in a slightly heavier weight (600) to read as data.\n\n"
        "**Color:** predominantly white/near-white surfaces (`#ffffff` / `#fbfbfd`) with dark text (`#0f1222`) for maximum clarity — the "
        "color budget is spent entirely on decoration, not surfaces. 2-3 mid-saturation brand colors (e.g. indigo `#5b5bd6`, sky "
        "`#3aa0ff`, and a warm pink/orange `#ff7a59`) used only in the gradient mesh and small accent details (icons, active nav item, "
        "chart lines) — never as large flat fills.\n\n"
        "**Gradient mesh background:** 2-4 large (400-800px), heavily blurred (100-140px) radial gradient blobs in the brand colors, "
        "layered behind hero/section content at 15-30% opacity, positioned asymmetrically (never centered/symmetric) so they read as an "
        "organic mesh rather than a simple two-tone gradient. This is the ONLY place saturated color appears at scale.\n\n"
        "**Cards:** solid white surface, no glass/blur, a very subtle shadow (`0 1px 2px rgba(16,24,40,0.06), 0 1px 3px "
        "rgba(16,24,40,0.1)`) and a 1px hairline border (`rgba(16,24,40,0.08)`); rounded corners 12-16px. Data/metric cards may have a "
        "thin colored top border (2-3px) matching that metric's status color.\n\n"
        "**Buttons:** solid brand-color fill for primary (subtle shadow, 8-10px radius, not full pill), outline/ghost for secondary. "
        "Icons are simple line icons (1.5-2px stroke), single color, no illustration.\n\n"
        "**Motion:** gradient blobs drift extremely slowly (40-60s) if animated at all — subtlety matters more than movement here. Cards "
        "lift very slightly on hover (`translateY(-2px)` + shadow increase, 150ms ease). Page transitions and data updates use quick, "
        "confident fades/slides (150-250ms), never bouncy."
    ),
)

ORGANIC_WELLNESS = StylePack(
    slug="organic_wellness",
    name="Organic Wellness",
    description="Earthy palette, hand-drawn/organic shapes, generous soft gradients. Warm, human, health/lifestyle-oriented.",
    when_to_use=("wellness", "health", "yoga", "food", "nature", "sustainability", "lifestyle", "organic"),
    spec=(
        "**Typography:** pair a warm serif or humanist display face for headings (weight 500-600, slightly larger than strictly "
        "necessary for a gentle, unhurried feel) with a clean humanist sans for body copy (400-450 weight, 16-18px, generous "
        "line-height 1.65+).\n\n"
        "**Color:** an earthy, desaturated palette — warm cream/sand background (`#f7f2ea`), deep forest or clay text (`#2e332b` or "
        "`#3b2f28`), and 2-3 muted accent tones drawn from nature (sage `#8ba888`, terracotta `#c97b5a`, warm gold `#d9a441`). Avoid "
        "pure white, pure black, or any neon/high-saturation color entirely.\n\n"
        "**Shapes:** organic, asymmetric blob shapes (irregular rounded polygons, not perfect circles) used for section backgrounds, "
        "image masks, and decorative accents — implement via `border-radius` with mismatched corner values (e.g. `60% 40% 30% 70% / "
        "60% 30% 70% 40%`) or a blob SVG mask. Avoid straight hard edges and perfect grids; let sections breathe with generous, "
        "uneven whitespace.\n\n"
        "**Texture:** a very subtle grain/noise overlay (2-3% opacity) over background colors adds warmth and avoids a flat digital "
        "look. Photography (where used) is warm-toned and naturally lit, never harsh studio lighting.\n\n"
        "**Buttons:** soft filled pill or organic-blob shape in an accent tone, low-contrast shadow (`0 4px 14px rgba(0,0,0,0.08)`), "
        "rounded generously. Ghost buttons use a thin accent-colored border with no fill.\n\n"
        "**Motion:** slow, breathing-paced animations only — blobs and gradients drift on 20-30s cycles, elements fade/rise in gently "
        "on scroll (400-600ms ease-out), nothing snaps or moves abruptly. The overall pacing should feel like an exhale."
    ),
)

RETRO_TERMINAL = StylePack(
    slug="retro_terminal",
    name="Retro Terminal",
    description="Monospace type on a CRT-green or amber-on-black terminal aesthetic, with scanlines and a blinking cursor motif. Nerdy, nostalgic, developer-facing.",
    when_to_use=("developer", "terminal", "cli", "retro", "hacker", "nostalgic", "geek", "programming"),
    spec=(
        "**Typography:** a single monospace face (JetBrains Mono / IBM Plex Mono / similar) for literally everything, including "
        "headings — vary size and weight, never switch families. Headings 700 weight, body 400-500. A blinking block or underscore "
        "cursor (`█` or `_`, `animation: blink 1s step-end infinite`) appears after key headings or as a decorative accent.\n\n"
        "**Color:** pure black or near-black background (`#0c0f0a`), single-hue monochrome phosphor palette — CRT green "
        "(`#33ff66`/`#7dffa3` for emphasis) or amber (`#ffb000`/`#ffd27a`) — pick ONE and use only tints/shades of it plus the black "
        "background; no second hue anywhere. Text has a very subtle glow (`text-shadow: 0 0 2px currentColor`) to sell the phosphor "
        "feel without going full neon.\n\n"
        "**Scanline/CRT texture:** a fixed full-viewport overlay of thin repeating horizontal scanlines (`repeating-linear-gradient`, "
        "1-2px lines, 3-5% opacity, `pointer-events:none`) plus a very subtle vignette darkening the viewport edges. Optionally a "
        "faint, slow flicker on the whole page (`opacity` oscillating 0.97-1.0 on a 4-6s irregular cycle) — keep it barely perceptible, "
        "not distracting.\n\n"
        "**Chrome/framing:** content sits inside a bordered \"terminal window\" panel — thin 1-2px border in the phosphor color at low "
        "opacity, sharp corners (0-4px radius), an optional top bar with three small dots (closed/idle style, not colorful macOS "
        "traffic lights) and a title like `~/project-name`. Prompts/labels are prefixed with `$`, `>`, or `#` as literal text.\n\n"
        "**Buttons/links:** text-only or thin-bordered rectangles, phosphor-colored, background transparent by default filling solid on "
        "hover/focus (color inversion: background becomes the phosphor color, text becomes black) — mimics a terminal's selection "
        "highlight.\n\n"
        "**Motion:** text can appear via a typewriter effect (character-by-character reveal) on key headings only, not every line. "
        "Otherwise motion is minimal and mechanical — instant or near-instant (80-120ms) state changes, no easing curves, no drifting "
        "shapes."
    ),
)

MINIMAL_MONO = StylePack(
    slug="minimal_mono",
    name="Minimal Mono",
    description="Pure black-and-white, oversized type, no color and no decoration at all. Confident, gallery-like, content is the only ornament.",
    when_to_use=("gallery", "art", "architecture", "luxury", "fashion", "monochrome", "high_end", "minimal"),
    spec=(
        "**Typography:** one high-contrast serif or a razor-clean grotesque for display headings, set VERY large "
        "(clamp(48px, 8vw, 140px)), weight 400-500 (contrast comes from size and spacing, not boldness), tight leading (0.95-1.0). "
        "Body copy in the same or a paired sans, 16-18px, weight 400, relaxed line-height (1.6+). No third typeface, no italics used "
        "decoratively.\n\n"
        "**Color:** strictly black and white (`#000000` / `#ffffff`), a single mid-gray (`#888`) permitted only for secondary/caption "
        "text. No accent color anywhere, no color at all in UI chrome — the only color permitted is in actual photographic content, "
        "if any.\n\n"
        "**Layout:** extreme whitespace — a single large image or headline often occupies most of the viewport with everything else "
        "pushed to the far edges. Content is strictly left- or right-aligned per section, rarely centered, and changes alignment "
        "between sections deliberately for rhythm. No cards, no borders, no shadows — sections are separated purely by whitespace and "
        "occasionally a single 1px black hairline.\n\n"
        "**Imagery:** full-bleed, high-contrast, black-and-white or desaturated photography treated as the primary content, not "
        "decoration — never cropped into rounded cards or given a shadow/border.\n\n"
        "**Buttons/links:** text-only, no fill, no border — an underline that animates in on hover (`background-size` trick or "
        "`text-decoration` transition) is the only interactive affordance. If a filled button is unavoidable, it is solid black with "
        "white text, sharp corners, no radius.\n\n"
        "**Motion:** slow, deliberate crossfades and large-scale image transitions (500-800ms ease) between states; text reveals as a "
        "simple fade or a mask-wipe. No bounce, no blur, no glow, no ambient background motion of any kind — stillness is the point."
    ),
)

STYLE_LIBRARY: tuple[StylePack, ...] = (
    LIQUID_GLASS,
    NEUBRUTALISM,
    SWISS_EDITORIAL,
    CYBERPUNK_NEON,
    SOFT_NEUMORPHISM,
    CORPORATE_GRADIENT_MESH,
    ORGANIC_WELLNESS,
    RETRO_TERMINAL,
    MINIMAL_MONO,
)

_BY_SLUG = {style.slug: style for style in STYLE_LIBRARY}


def get_style(slug: str) -> StylePack | None:
    return _BY_SLUG.get(slug)
