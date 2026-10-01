**The Dashboard's model status no longer goes stale.** The "Running" / "Preparing" indicator was
only ever checked once per page load and then left untouched - if your local model stopped after
that one check (crashed, was quit, machine slept), the Dashboard could keep claiming "Running"
indefinitely. It now keeps checking for as long as the page is open, so it reflects what's actually
running, not just what was running when the page loaded.
