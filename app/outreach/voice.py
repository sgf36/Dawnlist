"""Tone of voice, learned from the user's own sent messages.

## The constraint this design exists to satisfy

v1 ships **no mailbox reading** — no IMAP, no Gmail/Outlook OAuth, no app
passwords (handoff Part 1, Part 4). Learning a tone of voice from "historical
emails" would ordinarily mean exactly that, and would drag the product into
Google's restricted-scope rules and their annual paid CASA assessment.

So the messages arrive the same way job-alert emails already do: **the user
drags their own sent mail onto the app** — `.eml` files, or an `.mbox` export,
both of which every mainstream client can produce. Zero credentials, works with
every provider, and it is the user handling their own mail. Nothing here ever
connects to a mailbox.

## What is derived, and what is deliberately not

A tone profile is a set of MEASURED, checkable properties — greeting and
sign-off habits, sentence length, contraction rate, formality markers, whether
they open with pleasantries. Those are style, and style is safe to imitate.

What is never derived is CONTENT. The profile carries no employers, no figures,
no claims: every factual assertion in an outreach draft still comes from the
factsheet and nowhere else (spec 9.1, invariant 2). A tone profile that leaked
biography would be a route around the one rule that protects the user from
inventing their own career, which is why the extractor works on shape and
frequency rather than on sentences.
"""
from __future__ import annotations

import email
import email.policy
import mailbox
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

#: Lines that are quoted reply text, signatures or disclaimers — not the
#: user's own composition, so they must not shape the profile.
_QUOTE = re.compile(r"^\s*(>|On .{0,80}wrote:|-{2,}\s*$|_{2,}\s*$)", re.M)
_SIGNOFF_BLOCK = re.compile(
    r"\n\s*(sent from my |this e-?mail (and any )?attachments|"
    r"the information contained in this)", re.I)

_GREETINGS = [
    "dear", "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "greetings", "morning", "afternoon",
]
_SIGNOFFS = [
    "kind regards", "best regards", "warm regards", "many thanks",
    "with thanks", "thanks", "thank you", "regards", "best wishes",
    "best", "yours sincerely", "yours faithfully", "sincerely", "cheers",
    "all the best", "speak soon",
]
_CONTRACTIONS = re.compile(
    r"\b(i'm|i've|i'd|i'll|don't|doesn't|didn't|can't|won't|wouldn't|"
    r"couldn't|shouldn't|it's|that's|there's|we're|we've|you're|isn't|aren't)\b",
    re.I)
_HEDGES = re.compile(
    r"\b(perhaps|maybe|possibly|might|could|somewhat|fairly|rather|"
    r"i think|i believe|i wonder|if you have|whenever)\b", re.I)
_EXCLAIM = re.compile(r"!")

MIN_SAMPLE = 5


@dataclass
class VoiceProfile:
    """Measured style, never content."""
    sample_size: int = 0
    locale: str = "en"
    top_greetings: list[str] = field(default_factory=list)
    top_signoffs: list[str] = field(default_factory=list)
    median_sentence_words: float = 0.0
    median_paragraphs: float = 0.0
    median_words: float = 0.0
    contractions_per_100w: float = 0.0
    hedges_per_100w: float = 0.0
    exclamations_per_message: float = 0.0
    opens_with_pleasantry: float = 0.0

    @property
    def is_usable(self) -> bool:
        """Below the floor the profile is noise, and imitating noise is worse
        than using the neutral default. Reported, never silently assumed."""
        return self.sample_size >= MIN_SAMPLE

    @property
    def formality(self) -> str:
        """A coarse label, derived only from the measurements above."""
        score = 0
        if any(g in ("dear", "good morning", "good afternoon") for g in self.top_greetings):
            score += 2
        if any(s in ("kind regards", "yours sincerely", "yours faithfully")
               for s in self.top_signoffs):
            score += 2
        if self.contractions_per_100w < 1.0:
            score += 1
        if self.exclamations_per_message > 0.5:
            score -= 2
        if any(g in ("hi", "hey", "morning") for g in self.top_greetings):
            score -= 1
        if score >= 3:
            return "formal"
        if score <= -1:
            return "casual"
        return "neutral"

    def as_prompt(self) -> str:
        """The instruction block handed to the drafting model.

        Explicitly fences off content, because the model is being shown how the
        user writes and must not infer what the user has done.
        """
        if not self.is_usable:
            return ("No tone profile is available (fewer than "
                    f"{MIN_SAMPLE} sample messages). Write in a plain, warm, "
                    "professional register.")
        greet = ", ".join(self.top_greetings[:2]) or "no fixed greeting"
        sign = ", ".join(self.top_signoffs[:2]) or "no fixed sign-off"
        return (
            "Match this writer's habitual style, measured from messages they "
            f"wrote themselves ({self.sample_size} samples):\n"
            f"- register: {self.formality}\n"
            f"- opens with: {greet}\n"
            f"- signs off with: {sign}\n"
            f"- typical message length: about {self.median_words:.0f} words "
            f"in {self.median_paragraphs:.0f} paragraphs\n"
            f"- sentence length: about {self.median_sentence_words:.0f} words\n"
            f"- contractions: {self.contractions_per_100w:.1f} per 100 words "
            f"({'uses them freely' if self.contractions_per_100w >= 2 else 'uses them sparingly'})\n"
            f"- hedging: {self.hedges_per_100w:.1f} per 100 words\n"
            f"- exclamation marks: {self.exclamations_per_message:.2f} per message\n"
            "\nThis describes STYLE ONLY. It is not a source of facts. Every "
            "factual claim must still come from the background factsheet; "
            "anything the factsheet does not support stays a [[placeholder]]."
        )


def strip_quoted(text: str) -> str:
    """Keep only what the user actually composed."""
    cut = _SIGNOFF_BLOCK.search(text)
    if cut:
        text = text[:cut.start()]
    kept = [ln for ln in text.splitlines() if not _QUOTE.match(ln)]
    return "\n".join(kept).strip()


def _first_line_greeting(body: str) -> str | None:
    for line in body.splitlines():
        line = line.strip().lower()
        if not line:
            continue
        for g in sorted(_GREETINGS, key=len, reverse=True):
            if line.startswith(g):
                return g
        return None
    return None


def _signoff(body: str) -> str | None:
    tail = [ln.strip().lower().rstrip(",.!") for ln in body.splitlines() if ln.strip()]
    for line in reversed(tail[-4:]):
        for s in sorted(_SIGNOFFS, key=len, reverse=True):
            if line.startswith(s):
                return s
    return None


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def extract_bodies(paths: list[Path]) -> list[str]:
    """Read `.eml` files and `.mbox` exports into plain composed text."""
    bodies: list[str] = []
    for path in paths:
        try:
            if path.suffix.lower() == ".mbox":
                for msg in mailbox.mbox(str(path)):
                    body = _body_of(msg)
                    if body:
                        bodies.append(body)
            else:
                msg = email.message_from_bytes(path.read_bytes(),
                                               policy=email.policy.default)
                body = _body_of(msg)
                if body:
                    bodies.append(body)
        except Exception:  # noqa: BLE001 - one bad file never stops the import
            continue
    return bodies


def _body_of(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain",)) if hasattr(msg, "get_body") else None
        raw = part.get_content() if part is not None else None
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None:
        if msg.is_multipart():
            for p in msg.walk():
                if p.get_content_type() == "text/plain":
                    raw = p.get_payload(decode=True)
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    break
        else:
            raw = msg.get_payload(decode=True)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
    return strip_quoted(raw or "")


def build_profile(bodies: list[str], locale: str = "en") -> VoiceProfile:
    """Measure style across the user's own messages."""
    bodies = [b for b in bodies if b.strip()]
    if not bodies:
        return VoiceProfile(sample_size=0, locale=locale)

    greetings: Counter[str] = Counter()
    signoffs: Counter[str] = Counter()
    sentence_lengths: list[float] = []
    word_counts: list[int] = []
    para_counts: list[int] = []
    contractions = hedges = exclamations = total_words = 0

    for body in bodies:
        g = _first_line_greeting(body)
        if g:
            greetings[g] += 1
        s = _signoff(body)
        if s:
            signoffs[s] += 1

        words = re.findall(r"\b[\w']+\b", body)
        total_words += len(words)
        word_counts.append(len(words))
        para_counts.append(len([p for p in re.split(r"\n\s*\n", body) if p.strip()]))
        sents = _sentences(body)
        if sents:
            sentence_lengths.append(
                statistics.median(len(re.findall(r"\b[\w']+\b", s)) for s in sents))
        contractions += len(_CONTRACTIONS.findall(body))
        hedges += len(_HEDGES.findall(body))
        exclamations += len(_EXCLAIM.findall(body))

    per100 = (100.0 / total_words) if total_words else 0.0
    pleasantry = sum(1 for b in bodies
                     if re.search(r"\b(hope you('re| are)? (well|keeping well)|"
                                  r"trust you are well|hope this finds you)\b", b, re.I))

    return VoiceProfile(
        sample_size=len(bodies),
        locale=locale,
        top_greetings=[g for g, _ in greetings.most_common(3)],
        top_signoffs=[s for s, _ in signoffs.most_common(3)],
        median_sentence_words=statistics.median(sentence_lengths) if sentence_lengths else 0.0,
        median_paragraphs=statistics.median(para_counts) if para_counts else 0.0,
        median_words=statistics.median(word_counts) if word_counts else 0.0,
        contractions_per_100w=contractions * per100,
        hedges_per_100w=hedges * per100,
        exclamations_per_message=exclamations / len(bodies),
        opens_with_pleasantry=pleasantry / len(bodies),
    )


def profile_from_files(paths: list[Path], locale: str = "en") -> VoiceProfile:
    return build_profile(extract_bodies(paths), locale=locale)


# ---------------------------------------------------------------------------
# Drafting instructions: language + voice, together
# ---------------------------------------------------------------------------

def drafting_instructions(profile: VoiceProfile, target_locale: str,
                          locale_name: str) -> str:
    """The language and voice half of the drafting prompt.

    The language is stated explicitly rather than inferred from the fit brief:
    a draft silently written in the wrong language is not obviously wrong to a
    user who does not read it, and it goes to a real recipient.
    """
    rtl_note = ""
    if target_locale in {"ar", "ur", "fa", "he"}:
        rtl_note = ("\nThis language is written right-to-left. Do not add "
                    "directional marks or reorder punctuation manually; write "
                    "it naturally.")
    return (
        f"Write the entire message in {locale_name} ({target_locale}). "
        "Use the conventions a native business writer of that language would "
        "use for a cold approach — including how names, titles and greetings "
        "are normally handled. Do not translate an English draft literally."
        f"{rtl_note}\n\n{profile.as_prompt()}"
    )
