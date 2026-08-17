"""The fixed benchmark set: eight orders, never edited casually.

The point of a benchmark is that today's number is comparable with last week's, so these
descriptions are frozen inputs, not examples to improve. Changing one invalidates every
row already recorded against it -- if an order has to change, give it a new id and leave
the old one in place.

Chosen to cover the axes that actually move the pipeline's behaviour, not to look varied:

  * product type      -- static_page and web_app take different phases and different gates
  * complexity route  -- some trip a complexity keyword ("offline", "notification",
                         "concurrent"), some do not, so both branches of the model router
                         get exercised (order_workflow/complexity.py)
  * screen count      -- a single widget and a multi-screen app break differently at 375px,
                         which is where the visual gate earns its keep
  * backend need      -- all are single-local-user on purpose, so decide_backend_need stays
                         a constant across the set and does not confound the comparison

Five are small (one screen or one page) and three are realistic multi-screen applications,
matching the split the MVP acceptance run uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


BenchKind = Literal["static_page", "small_app", "realistic_app"]


@dataclass(frozen=True, slots=True)
class BenchOrder:
    id: str
    kind: BenchKind
    title: str
    description: str
    product_type: str
    # What this order exists to exercise. Recorded so a row that fails is read against the
    # thing it was chosen for, rather than as a generic "one of eight failed".
    covers: str


BENCH_ORDERS: tuple[BenchOrder, ...] = (
    BenchOrder(
        id="b01-profile-card",
        kind="static_page",
        title="Profile Card",
        description=(
            "A single page showing my name, a round photo placeholder, one sentence about what "
            "I do, and three links to email, GitHub and LinkedIn as labelled buttons."
        ),
        product_type="static_page",
        covers="the simplest possible order: one page, no state, no complexity keywords -- routes routine",
    ),
    BenchOrder(
        id="b02-pricing-page",
        kind="static_page",
        title="Pricing Page",
        description=(
            "A single page with three pricing tiers side by side -- Free, Pro and Team -- each "
            "with a price, four bullet points of what is included, and a call-to-action button. "
            "The middle tier is visually marked as the recommended one."
        ),
        product_type="static_page",
        covers="three columns on one page: the layout that overflows a 375px viewport if nothing stops it",
    ),
    BenchOrder(
        id="b03-focus-timer",
        kind="small_app",
        title="Focus Timer",
        description=(
            "A pomodoro focus timer that runs entirely offline in the browser. The user can "
            "start, pause and reset a 25-minute focus session, sees a large animated countdown "
            "ring, and gets a desktop notification when the session ends. Completed sessions "
            "for the day are shown as a simple streak of dots."
        ),
        product_type="web_app",
        covers="the historical reference order: trips 'offline' and 'notification', so both phases route complex",
    ),
    BenchOrder(
        id="b04-tip-splitter",
        kind="small_app",
        title="Tip Splitter",
        description=(
            "A one-screen calculator for splitting a restaurant bill. I type the total, choose a "
            "tip percentage from preset buttons or type my own, and set how many people are "
            "sharing. It shows the tip amount, the total with tip, and the amount per person, "
            "updating as I type."
        ),
        product_type="web_app",
        covers="one screen, live-computed state, no persistence -- the cheapest web_app path",
    ),
    BenchOrder(
        id="b05-habit-tracker",
        kind="small_app",
        title="Habit Tracker",
        description=(
            "A single screen where I add habits by name and tick each one off for today. Each "
            "habit shows how many days in a row I have ticked it. Everything stays in this "
            "browser on my own laptop and survives a page reload."
        ),
        product_type="web_app",
        covers="persistence across reload: the state-continuity gate's actual subject",
    ),
    BenchOrder(
        id="b06-reading-journal",
        kind="realistic_app",
        title="Reading Journal",
        description=(
            "A personal reading journal that lives entirely in one browser on my own laptop -- "
            "nobody else uses it and nothing leaves the machine. I add a book by typing its "
            "title, author and page count, and it goes onto one of three shelves: Want to read, "
            "Reading, Finished. I drag a book from shelf to shelf as I go, and for a book on the "
            "Reading shelf I record the page I stopped at and see a progress bar fill in. "
            "Opening a book shows its own page where I write free-form notes and favourite "
            "quotes, saved as I type. A search box filters the shelves by title or author."
        ),
        product_type="web_app",
        covers="the run that failed live on 2026-08-17: three shelves, drag and drop, per-item detail page",
    ),
    BenchOrder(
        id="b07-recipe-box",
        kind="realistic_app",
        title="Recipe Box",
        description=(
            "A recipe box I keep in one browser on my own laptop -- nobody else uses it and "
            "nothing leaves the machine. I add a recipe by typing its name, the ingredients as a "
            "list, and the steps. Recipes appear as cards I can filter by a tag such as "
            "breakfast or baking, and a search box finds them by name or ingredient. Opening a "
            "recipe shows its own page with the ingredients as a checklist I tick while cooking "
            "and the steps numbered underneath."
        ),
        product_type="web_app",
        covers="card grid, tag filter, per-item detail with its own interactive state",
    ),
    BenchOrder(
        id="b08-invoice-log",
        kind="realistic_app",
        title="Invoice Log",
        description=(
            "A log of the invoices I send, kept in this browser on my own laptop. Each invoice "
            "has a client name, a number, an amount, a date sent and a status of draft, sent or "
            "paid. I see them in a table I can sort by date or amount and filter by status, with "
            "the total outstanding shown above it. Overdue invoices -- sent more than 30 days ago "
            "and still unpaid -- are marked so I notice them. Everything survives a reload."
        ),
        product_type="web_app",
        covers="a sortable table with derived values and a date rule: the closest thing here to real business logic",
    ),
)


BENCH_ORDERS_BY_ID = {order.id: order for order in BENCH_ORDERS}


def order_payload(order: BenchOrder) -> dict:
    """The POST /api/orders body, matching the demo runner's shape."""
    return {
        "title": order.title,
        "description": order.description,
        "product_type": order.product_type,
        "preferred_language": "en",
        "constraints": [],
    }
