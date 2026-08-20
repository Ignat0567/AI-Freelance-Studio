"""Say so when the order's own words disagree with what the brief resolved.

One live run on 2026-08-18 produced three of these at once. The description said "single
self-contained page" five times and the product type said web application, so the pipeline
built a Vite app. The description said "no login, no password, the only input is a
newsletter email" and the resolved audience implied multiple users, so the backend gate
concluded server-side state was needed and a full FastAPI backend with a database was built
for a presentation page. Each step was defensible on its own inputs; nobody compared the
inputs with what the client had actually written.

The structured brief wins, and should -- it is what every later stage reads. What was
missing is that the disagreement was never mentioned to anyone. So these notes land in
`ProjectBrief.assumptions`, which the client already reads on the approval screen, and each
one names which side will be followed. A client who meant the other thing can fix it before
approving, for free, instead of discovering it in a delivered folder.

Deliberately not a model call and deliberately not a blocker:

* A keyword comparison over text the pipeline already has is instant, costs nothing, and is
  wrong legibly -- the lists below can be read and argued with.
* Blocking would stop the autopilot, the bench runner and every unattended run on an order
  that is very often exactly what its author intended. A note is enough: the failure mode
  here is a client who was never told, not a client who was not stopped.
"""

from __future__ import annotations

from .models import ProductType, ProjectBrief, UserOrder

# Phrases people use when they mean one page, not an application with screens.
_SINGLE_PAGE_SIGNALS = (
    "single self-contained page",
    "self-contained page",
    "single page",
    "one page",
    "single-file",
    "single file page",
    "landing page",
    "presentation page",
    "one html file",
)

# Phrases that only make sense for something with more than one screen.
_MULTI_SCREEN_SIGNALS = (
    "several screens",
    "multiple screens",
    "multi-screen",
    "navigation between",
    "navigate between",
    "its own page for",
    "dashboard with",
)

# Phrases that say, plainly, that nothing is shared and nobody signs in.
_PRIVATE_SIGNALS = (
    "no login",
    "no password",
    "no sign-in",
    "no sign in",
    "no account",
    "no accounts",
    "nothing leaves the machine",
    "only i use",
    "nobody else uses it",
    "single local user",
)

# Audience wording that makes the backend gate conclude server-side state is required.
_SHARED_AUDIENCE_SIGNALS = ("team", "customers", "public", "colleagues", "clients", "staff", "users of")


def _mentions(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def reconcile_description_with_brief(order: UserOrder, brief: ProjectBrief) -> tuple[str, ...]:
    """Notes for anything the description asks for that the brief resolved differently.

    Empty when they agree, which is the common case -- a note that appears on every order
    teaches people to skip the section it appears in.
    """
    description = f"{order.title} {order.description}".casefold()
    notes: list[str] = []

    if brief.product_type is ProductType.WEB_APP and _mentions(description, _SINGLE_PAGE_SIGNALS):
        notes.append(
            "Your description asks for a single self-contained page, but this order's product "
            "type is a small web application. The build follows the product type -- change it "
            "on the order if you meant one page."
        )
    elif brief.product_type is ProductType.STATIC_PAGE and _mentions(description, _MULTI_SCREEN_SIGNALS):
        notes.append(
            "Your description asks for several screens, but this order's product type is a "
            "single interactive page. The build follows the product type -- change it on the "
            "order if you meant an application."
        )

    audience = " ".join(brief.target_users).casefold()
    if _mentions(description, _PRIVATE_SIGNALS) and _mentions(audience, _SHARED_AUDIENCE_SIGNALS):
        notes.append(
            # Sliced hard: assumptions are ShortText (240 chars), and an audience list is
            # free text that a client can make arbitrarily long.
            f"Your description says nobody signs in, but the audience was recorded as "
            f"\"{', '.join(brief.target_users)[:40]}\". A shared audience makes the build add a "
            "server and a database. Narrow it if this is for one person."
        )

    return tuple(notes)
