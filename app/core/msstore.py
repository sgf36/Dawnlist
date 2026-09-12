"""The Microsoft Store's own subscription, for the `store_iap` build.

WHY THIS EXISTS BESIDE THE PADDLE PATH RATHER THAN REPLACING IT
---------------------------------------------------------------
Paddle declined `dawnlist.spencerfields.com` on 11 September 2026, placing it
under "Advertising and Marketing/Job Boards" in their Acceptable Use Policy. An
appeal is open and Paddle billing is cheaper, so the Paddle path is not going
anywhere — it is a build variant away, not a revert away. `store_iap` and
`store` are separate variants precisely so both stay compiled, tested and in CI
while the appeal runs: whichever wins is a packaging flag, not a code change
somebody has to remember how to undo.

WHAT THE STORE CAN AND CANNOT TELL US
-------------------------------------
`StoreContext.GetAppLicenseAsync()` returns the licences this user holds for
this app, including add-ons, and it is available offline from the local
licence. That is enough to decide what the WINDOW shows — whether to offer a
purchase or a shortlist — and it is not enough to entitle the feed.

The feed is metered per licence in the Worker, so a Store subscriber still
needs a Worker licence, and the Worker cannot take this process's word for it:
anything this module reports could be produced by a patched build. Server-side
proof is a Store ID key from `GetCustomerCollectionsIdAsync`, which the Worker
exchanges through Microsoft's collections API. That is the `/v1/microsoft`
half, and it needs an Azure AD registration that does not exist yet.

So this module deliberately reports TWO different things and never conflates
them: `local_state()` — what the machine believes, good enough for the screen —
and `collections_key()` — the evidence the Worker will accept. A single
"is_subscribed" would have callers using a local answer where a proven one was
required, which is the shape of every entitlement bug this project has had.

WINRT IS ALREADY A DEPENDENCY. The startup-task work brought
`winrt-runtime` and the `Windows.ApplicationModel` projection in, so the bridge
from Python is proven on this stack rather than assumed. This adds only
`Windows.Services.Store`.

NOTHING HERE WORKS WITHOUT PACKAGE IDENTITY. `StoreContext` requires the
process to be running from an MSIX; a direct-download build has no identity and
every call fails. That is not a bug to route around — it is why `store_iap` is
its own variant and why `direct` keeps Paddle.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The add-on as Partner Center knows it. Store IDs are not guessable and not
#: derivable from the product id, so it is recorded here and nowhere else.
#: Confirmed in Partner Center: com.spencerfields.dawnlist.monthly.
ADD_ON_STORE_ID = "9P0THQRBKFPF"

#: The app itself, for the diagnostics only. `--doctor` prints it so a package
#: that is silently the wrong product is visible without unpacking it.
APP_STORE_ID = "9PF25H395BB8"


class StoreUnavailable(RuntimeError):
    """The Store could not be asked at all.

    NOT the same as "no subscription", and the difference decides what the user
    is told. No identity, no WinRT, no network, a Store service outage — none
    of them are evidence that somebody has not paid, and reporting them as
    "not subscribed" tells a paying customer they never bought anything. The
    same distinction `credentials.KeyringUnavailable` draws, for the same
    reason.
    """


@dataclass(frozen=True)
class LocalState:
    """What this machine believes about the subscription.

    `active` is good enough to decide what the window shows and NOT good enough
    to entitle the feed — see the module docstring. `expires_at` is whatever
    the Store reported, which may be absent.
    """

    active: bool
    expires_at: str | None = None
    #: True when the Store answered at all. False means the question could not
    #: be asked, which is `StoreUnavailable` territory rather than a refusal.
    asked: bool = True


def _context():
    """A `StoreContext` for this process, or raise `StoreUnavailable`.

    Imported inside the function rather than at module scope so that importing
    this module on macOS, on a direct build, or in a test collection run does
    not fail. Every import error here means the same thing to a caller — the
    Store cannot be asked — so they are collapsed into one exception rather
    than leaking three different ImportErrors upward.
    """
    try:
        from winrt.windows.services.store import StoreContext
    except Exception as exc:  # noqa: BLE001 - any import failure is the same answer
        raise StoreUnavailable(f"Windows.Services.Store unavailable: {exc}") from exc

    try:
        return StoreContext.get_default()
    except Exception as exc:  # noqa: BLE001
        # The usual cause is no package identity: a direct-download build, or
        # running from source. Said plainly, because "the Store refused" sends
        # somebody looking at their Microsoft account.
        raise StoreUnavailable(
            f"no Store context — is this running from an MSIX? ({exc})") from exc


def local_state(*, context=None) -> LocalState:
    """Does this machine hold the add-on licence?

    Reads the app licence, which the Store keeps locally, so this answers
    without a network round trip and keeps answering during an outage. That is
    the point: a subscriber who is offline should still see their shortlist.
    """
    ctx = context or _context()
    try:
        licence = ctx.get_app_license_async().get()
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"could not read the app licence: {exc}") from exc

    add_ons = getattr(licence, "add_on_licenses", None) or {}
    for key, add_on in dict(add_ons).items():
        # Matched on the add-on's OWN store id rather than the dictionary key.
        # The key is a "Store ID key" in some SDK versions and the product id
        # in others, and a lookup that happened to work on one machine is
        # exactly the kind of thing that fails silently on somebody else's.
        store_id = getattr(add_on, "sku_store_id", "") or getattr(add_on, "in_app_offer_token", "")
        if not str(store_id).startswith(ADD_ON_STORE_ID):
            continue
        if not getattr(add_on, "is_active", False):
            continue
        expires = getattr(add_on, "expiration_date", None)
        return LocalState(True, _iso(expires))
    return LocalState(False)


def _iso(value) -> str | None:
    """A WinRT DateTime as ISO 8601, or None. Never raises: an expiry we cannot
    read is missing information, not a reason to refuse somebody."""
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:  # noqa: BLE001
        return None


def collections_key(*, context=None, service_ticket: str = "",
                    publisher_user_id: str = "") -> str:
    """The Store ID key the WORKER can verify, not this process's opinion.

    This is the only artefact worth sending to a server. It is signed by the
    Store and exchanged through Microsoft's collections API, so a patched
    client cannot manufacture one — which `local_state` plainly could.

    Raises `StoreUnavailable` when the Store cannot produce one. A caller must
    not fall back to `local_state` here: that would turn a proof into a claim,
    and the whole reason for two functions is that nothing should be able to do
    that by accident.
    """
    ctx = context or _context()
    try:
        result = ctx.get_customer_collections_id_async(
            service_ticket, publisher_user_id).get()
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"could not get a collections id: {exc}") from exc
    if not result:
        raise StoreUnavailable("the Store returned no collections id")
    return str(result)


@dataclass(frozen=True)
class Offer:
    """What the Store says this subscription costs, for the purchase screen.

    `price` is Microsoft's own formatted string — "£79.00", "79,00 €" — in the
    customer's market and currency. NEVER build one from a number here: the
    Store sells in every market it is available in, and a price formatted by
    this app would be wrong in most of them and differ from what the customer
    is actually charged at the till.
    """

    title: str
    price: str
    #: False when the Store could be asked but does not offer this add-on to
    #: this customer — a market where it is not sold. Distinct from
    #: `StoreUnavailable`, which is not having been able to ask.
    available: bool = True


def offer(*, context=None) -> Offer:
    """Title and price, asked of the Store."""
    ctx = context or _context()
    try:
        result = ctx.get_associated_store_products_async(["Durable"]).get()
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"could not read the add-on: {exc}") from exc

    products = getattr(result, "products", None) or {}
    for key, product in dict(products).items():
        if not str(getattr(product, "store_id", key)).startswith(ADD_ON_STORE_ID):
            continue
        price = getattr(getattr(product, "price", None), "formatted_price", "")
        return Offer(str(getattr(product, "title", "") or ""), str(price or ""))
    return Offer("", "", available=False)


#: Microsoft's StorePurchaseStatus, by value. Named here rather than compared
#: as integers at the call site: `status == 1` is not readable, and 1 is the
#: case that looks like failure and is not.
PURCHASE_STATUS = {
    0: "succeeded",
    1: "already",        # already owned — a success, not an error
    2: "cancelled",      # the customer closed the dialog
    3: "network",
    4: "server",
}


def purchase(*, context=None) -> str:
    """Ask the Store to sell the subscription. Returns a PURCHASE_STATUS value.

    ALREADY-OWNED IS NOT A FAILURE. Microsoft returns `AlreadyPurchased` when
    the customer holds the add-on on this account — after a reinstall, or on a
    second machine — and a screen that reported that as an error would tell
    somebody who has paid that their payment did not work. It is folded into
    success by the caller, and named here so that cannot be done by accident.

    CANCELLED IS NOT A FAILURE EITHER. Closing the dialog is a decision, and
    saying "purchase failed" to somebody who chose not to buy is both wrong and
    faintly insulting.
    """
    ctx = context or _context()
    try:
        result = ctx.request_purchase_async(ADD_ON_STORE_ID).get()
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"the purchase could not be started: {exc}") from exc
    status = getattr(result, "status", None)
    return PURCHASE_STATUS.get(int(status) if status is not None else -1, "unknown")
