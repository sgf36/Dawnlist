# ReleaseNotes — en-GB source, for 1.1.0

The live build (1.0.0) refuses every licence key: it verified them against a
service endpoint that answers before it reads the request, and the network in
front of that service refused the app's requests outright. Anyone who had a
key was told it was not accepted.

Release notes are read by people deciding whether to update. This one has to
say that plainly without inviting them to conclude the product is unreliable.

---

Fixes a fault that stopped licence keys and access codes being accepted at
all. If Dawnlist told you your key was not valid, it will work now.

Setting up is clearer: it asks for your subscription in the right place
rather than leaving you to find it, the suggested searches are real job
titles, and answers you type during setup are visibly saved.

Dawnlist now opens in your computer's language, and you can change it at any
time from the ⋯ menu.

---

## What this deliberately does not say

- **No apology paragraph.** A release note is not the place; the fix is the
  apology.
- **No detail about the cause.** "A network filter refused our own client's
  requests" is true and tells a customer nothing they can act on.
- **No mention of Restore or StoreKit.** Both are Mac App Store only and this
  is the Windows listing.
