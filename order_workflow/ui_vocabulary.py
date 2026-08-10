from __future__ import annotations

from dataclasses import dataclass

# Source: https://namethatui.com/ -- web-category entries only (the site also documents
# macOS/AppKit patterns, irrelevant to Studio's React/Vite web output, so those are
# deliberately excluded rather than carried as dead weight).
# Purpose: name UI elements precisely (real pattern name + its ARIA/HTML/CSS/API symbol)
# instead of vague descriptions, so a coding prompt says "role=\"tablist\" (Tabs)" rather
# than "add some tabs".


@dataclass(frozen=True, slots=True)
class UIPattern:
    name: str
    api_symbol: str
    description: str


WEB_UI_VOCABULARY: tuple[UIPattern, ...] = (
    UIPattern("Steps", 'aria-current="step"', "The numbered circles across the top of a checkout or wizard, one per stage"),
    UIPattern("Avatar Group", "AvatarGroup", "Overlapping profile circles with a ring between them and a +N at the end"),
    UIPattern("Multi-select", "<select multiple>", "One control holding several values: checkbox dropdown, chip field, or two-pane transfer list"),
    UIPattern("Scrollspy", "IntersectionObserver", "The On-this-page list whose current link follows what you're reading"),
    UIPattern("Inline Alert vs. Callout vs. Banner", '<Alert severity="warning">', "Three in-page notices named by where they sit, none of them is a toast"),
    UIPattern("Sign-in Form", 'autocomplete="current-password"', "The login form's nameable parts: the eye, the OR line, the Continue-with buttons"),
    UIPattern("Pagination", '<nav aria-label="pagination">', "The numbered page buttons under a list, and the dot version (page control)"),
    UIPattern("Date Picker", '<input type="date">', "The little calendar that pops up on a date field, and the highlighted range stripe"),
    UIPattern("Parallax Scrolling", "animation-timeline: scroll()", "Layers that scroll at different speeds — the background lags and depth appears"),
    UIPattern("Carousel", 'aria-roledescription="carousel"', "A strip of slides you page through with arrows or dots"),
    UIPattern("Site Header vs. Navigation Bar", "<header>", "The whole top strip is the header; the row of page links inside it is the nav"),
    UIPattern("Card", "<Card>", "The rectangle with media, title, body, and a footer — every part has a name"),
    UIPattern("Resize Handle (Size Grip)", "resize", "The three diagonal lines in a text box's corner that you drag to resize"),
    UIPattern("Hamburger Menu (Nav Drawer)", "aria-expanded + aria-controls", "The three-line button and the navigation panel it slides open"),
    UIPattern("Bento Grid", "display: grid + grid-column: span 2", "One grid, mixed tile sizes, a layout packed like a bento box"),
    UIPattern("Masonry Layout (Pinterest Grid)", "columns", "Cards of different heights packed into columns with no row gaps"),
    UIPattern("Easing (Timing Function)", "transition-timing-function", "The speed curve of an animation — why motion feels smooth or robotic"),
    UIPattern("Spring Animation", 'transition={{ type: "spring", stiffness, damping }}', "Physics-based motion that overshoots the target and settles"),
    UIPattern("Text Scramble (Decode Effect)", "ScrambleTextPlugin", "Random characters churn and settle into the real text"),
    UIPattern("Lightbox", "<dialog>", "The click-to-enlarge image overlay that dims the page behind it"),
    UIPattern("Marquee", "animation + @keyframes translateX", "Content that auto-scrolls sideways in an endless loop"),
    UIPattern("Form Field", "<label for>", "Every part of a labeled input — label, placeholder, helper text, error line"),
    UIPattern("Truncation (Ellipsis & Line Clamp)", "text-overflow: ellipsis", "Text cut short with … at end of line, after N lines, or in the middle"),
    UIPattern("Drag & Drop", "ondrop", "The grips, handles, previews, and landing cues around a drag interaction"),
    UIPattern("Divider vs. Separator vs. Rule", "<hr>", "The same thin line can mark a topic break, separate controls, or be decoration"),
    UIPattern("Progress Ring vs. Spinner vs. Progress Bar", "<progress>", "A spinner means wait; a ring or bar shows how much work is complete"),
    UIPattern("The Three Dots (Overflow Menu)", "<button>", "Horizontal dots, vertical dots, three lines, and an ellipsis mean different things"),
    UIPattern("Toast (Snackbar)", 'role="status"', "A brief, non-blocking message that appears after an action"),
    UIPattern("Modal Dialog vs. Drawer vs. Sheet", "<dialog>", "Three overlay patterns distinguished by placement, scope, and task depth"),
    UIPattern("Popover vs. Dropdown Menu vs. Tooltip", "popover", "Three anchored overlays with different triggers, content, and dismissal rules"),
    UIPattern("Scrim (Backdrop / Overlay)", "::backdrop", "The translucent layer that separates a modal surface from the page"),
    UIPattern("Skeleton vs. Spinner", 'aria-busy="true"', "Two loading indicators for predictable layouts and indeterminate waits"),
    UIPattern("Combobox (Autocomplete / Typeahead)", 'role="combobox"', "A text input paired with a filtered list of selectable suggestions"),
    UIPattern("Command Palette", "Command", "A keyboard-first searchable launcher for actions and navigation"),
    UIPattern("Accordion (Disclosure)", "<details>", "Stacked sections whose headings expand and collapse their content"),
    UIPattern("Tabs", 'role="tablist"', "A single row of labels that switches one shared content region"),
    UIPattern("Badge vs. Chip vs. Pill vs. Tag", "Badge", "Compact labels distinguished by meaning, shape, and interactivity"),
    UIPattern("Breadcrumbs", "<nav>", "A hierarchy trail from the current page back to its ancestors"),
    UIPattern("Sticky vs. Fixed Positioning", "position: sticky", "Two ways to keep an element visible with different containing blocks"),
    UIPattern("Focus Ring (:focus-visible)", ":focus-visible", "The keyboard-aware outline that identifies the active control"),
    UIPattern("Empty State", "<section>", "Purposeful guidance shown when a view has no content yet"),
    UIPattern("Hover Card", "HoverCard", "A rich, non-modal preview revealed from a hovered or focused reference"),
    UIPattern("Switch vs. Checkbox vs. Radio", '<input type="checkbox" role="switch">', "Controls for an on/off setting, independent choices, or one choice from a group"),
    UIPattern("Toggle Group (Segmented Control)", "ToggleGroup", "A connected row of compact options with one persistent selection"),
)

_BY_NAME = {pattern.name: pattern for pattern in WEB_UI_VOCABULARY}


def get_pattern(name: str) -> UIPattern | None:
    return _BY_NAME.get(name)
