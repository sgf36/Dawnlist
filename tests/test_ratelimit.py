"""The rate limiter enforces EVERY window, not just the headline one."""
import app.feed.base as base
from app.feed.base import RateLimiter


class FakeClock:
    """Time under our control: the test must not actually sleep."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


def limiter(monkeypatch, **kw):
    clock = FakeClock()
    monkeypatch.setattr(base.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(base.time, "sleep", clock.sleep)
    return RateLimiter(**kw), clock


def test_the_hourly_cap_binds_before_the_per_minute_one(monkeypatch):
    """The measured P0 fault: 6-second spacing satisfies 10/minute and blows
    50/hour after eight minutes."""
    rl, clock = limiter(monkeypatch, per_second=4, per_minute=10,
                        per_hour=50, per_day=400)
    start = clock.now
    for _ in range(50):
        rl.wait()
    # 50 calls fit inside the hour...
    assert clock.now - start < 3600
    # ...and the 51st must wait for the first to age out of the hour window.
    rl.wait()
    assert clock.now - start >= 3600, "the hourly cap was not enforced"


def test_the_per_minute_cap_is_still_enforced(monkeypatch):
    rl, clock = limiter(monkeypatch, per_second=100, per_minute=10,
                        per_hour=10_000, per_day=10_000)
    start = clock.now
    for _ in range(11):
        rl.wait()
    assert clock.now - start >= 60


def test_the_per_second_cap_is_still_enforced(monkeypatch):
    rl, clock = limiter(monkeypatch, per_second=4, per_minute=10_000,
                        per_hour=10_000, per_day=10_000)
    start = clock.now
    for _ in range(5):
        rl.wait()
    assert clock.now - start >= 1.0


def test_a_burst_within_every_cap_does_not_sleep(monkeypatch):
    rl, clock = limiter(monkeypatch, per_second=4, per_minute=10,
                        per_hour=50, per_day=400)
    start = clock.now
    for _ in range(4):
        rl.wait()
    assert clock.now == start


def test_would_wait_reports_without_sleeping(monkeypatch):
    rl, clock = limiter(monkeypatch, per_second=1, per_minute=10,
                        per_hour=50, per_day=400)
    rl.wait()
    before = clock.now
    assert rl.would_wait() > 0
    assert clock.now == before, "would_wait must never sleep"


# -- the employer's name, whichever shape the row carries ------------------
def test_the_flat_company_name_is_preferred():
    from app.feed.theirstack import _company_name
    assert _company_name({"company_name": "NOVALAND",
                          "company": '{"name": "wrong"}'}) == "NOVALAND"


def test_a_json_encoded_company_is_decoded():
    """Measured against a real 2,000-row dataset sample: `company` is a JSON
    STRING there, so it is truthy, is a `str`, and passes every "did we get a
    company" check while being 700-2,200 characters of JSON. Every row would
    have shown that blob as the employer — on the board, in the company column,
    in `known_employers`, in kill-family matching, in the dedup name key and in
    the drafting prompt."""
    from app.feed.theirstack import _company_name
    blob = ('{"id":"7f90671786a91654d52153aaa5af5124","name":"NOVALAND",'
            '"domain":"tuyendung.novaland.com.vn","employee_count":2100}')
    assert _company_name({"company": blob}) == "NOVALAND"


def test_a_company_object_still_works():
    """The API shape, which must not regress."""
    from app.feed.theirstack import _company_name
    assert _company_name({"company": {"name": "Acme Hotels"}}) == "Acme Hotels"
    assert _company_name({"company_object": {"name": "Acme"}}) == "Acme"


def test_a_plain_company_string_is_kept():
    from app.feed.theirstack import _company_name
    assert _company_name({"company": "Acme Hotels"}) == "Acme Hotels"


def test_unparseable_json_yields_nothing_rather_than_a_blob():
    """Better an empty employer the audit will flag than 2kB of JSON on the
    board."""
    from app.feed.theirstack import _company_name
    assert _company_name({"company": '{"name": broken'}) == ""


def test_a_missing_company_is_empty():
    from app.feed.theirstack import _company_name
    assert _company_name({}) == ""
    assert _company_name({"company": None, "company_name": "  "}) == ""
