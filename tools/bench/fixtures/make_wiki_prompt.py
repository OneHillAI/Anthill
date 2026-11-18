"""Build tools/bench/fixtures/scoreboard_wiki_prompt.json: the messages Anthill sends for one wiki question (#236).

Run by hand from the repo root (python tools/bench/fixtures/make_wiki_prompt.py) when the prompt builder changes.
The three pages are synthetic text written for this fixture (no one's data); the prompt is built with the real
anthill.wiki.prompts.answer_question and the real page separator, so the scoreboard measures a prompt shaped
like a real answer without depending on the wiki code, retrieval or an embedding model at run time.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from anthill.wiki.passages import PAGE_SEPARATOR
from anthill.wiki.prompts import answer_question

QUESTION = "A member received a damaged crate of olive oil. What are the steps and the time limits to get it replaced?"

PAGES = [
    """# Orders and shipping

Harbor Cooperative ships from two warehouses: the north dock in Tallis Bay and the south dock in Merrow. An order
is routed to whichever dock holds all of its items; if the items are split across both docks, the order is split
into two parcels and the member is charged shipping once, on the heavier parcel. Orders placed before 14:00 local
time on a working day are packed the same day. Orders placed after that, or on a weekend or a public holiday, are
packed on the next working day. Packing takes between two and six hours depending on the queue, and the member
receives a tracking message when the parcel is handed to the carrier.

Standard delivery takes three to five working days inside the region and six to nine working days to the islands.
Express delivery takes one to two working days inside the region and is not offered to the islands. Cold-chain
items (dairy, fresh fish, fresh herbs) ship only on Monday to Wednesday, in insulated boxes with a gel pack, so that
they never wait over a weekend in a depot. A cold-chain parcel that is refused at the door must be reported within
two hours, because the contents cannot be restocked.

Bulky items such as crates of bottled oil, sacks of flour above ten kilograms and barrels of vinegar travel by pallet
carrier. A pallet carrier needs a delivery window, which the member books online after the order is packed. The
carrier phones the member the evening before. If nobody is available at the address twice, the pallet returns to
the dock and the member pays the return freight. Every parcel carries a delivery note with the order number, the
packer's initials and a photograph of the sealed box, so that damage in transit can be told apart from damage in
the warehouse. Members are asked to open the box in front of the carrier when the item is fragile or heavy and to
note any visible damage on the carrier's device before signing.

Shipping costs are shown at checkout and are never changed after the order is placed. Free shipping applies to
orders above one hundred and twenty euros inside the region. The free shipping threshold counts the price of the
goods after discounts and before taxes. Gift orders can be sent to a different address, and the delivery note then
omits prices. A member can change the address until the parcel is handed to the carrier by writing to the support
desk with the order number; after that the carrier's redirect service must be used and it carries its own fee.
""",
    """# Returns, damage and replacements

A member can return any unopened item within thirty days of delivery for a full refund of the goods. Opened items
can be returned within thirty days only if they are faulty. Perishable items cannot be returned, but if they arrive
spoiled or damaged the cooperative replaces or refunds them under the damage procedure below.

The damage procedure has four steps. First, the member photographs the damaged goods, the outer box and the delivery
note, and keeps the packaging. Second, the member opens a damage claim in the account area within forty-eight hours
of delivery for ordinary items, or within two hours for cold-chain items, quoting the order number and attaching the
photographs. Third, the support desk answers within one working day: it either approves a replacement, approves a
refund, or asks for more evidence. Fourth, if a replacement is approved, it ships free of charge on the next packing
day from the dock that holds the item; if a refund is approved, it returns to the original payment method within five
working days. Claims opened after the deadline are reviewed case by case and are approved only when the carrier
confirms a delivery incident.

Heavy damage, such as a crushed pallet or a leaking barrel, is handled by the carrier's claims team in parallel.
The member does not have to wait for the carrier: the cooperative replaces the goods first and recovers the cost from
the carrier afterwards. For crates of bottled oil, one broken bottle does not void the crate. The cooperative
replaces only the broken bottles and ships them in a padded sleeve, unless more than a third of the bottles are
broken, in which case the whole crate is replaced. A replacement that is itself damaged is escalated to the quality
lead, who calls the member within one working day.

Refunds never include the cost of a return that the member chose to make, with the exception of faulty goods, where
the return label is prepaid. Members who are unhappy with a decision can ask for a second review by the quality lead,
whose answer is final. Every claim is logged with its photographs for twelve months, which lets the purchasing team
spot suppliers whose goods travel badly. A supplier with three confirmed claims in a quarter receives a written
warning, and with five the cooperative stops reordering until the packaging is redesigned.
""",
    """# Member accounts and support hours

Every member has an account with a unique member number, a delivery address book, a payment method and a claims
history. The account area is the only place where claims and address changes are opened, so that every request has
an order number and a timestamp. Members who cannot sign in can ask the support desk to send a one-time link to the
email address on file; the desk never asks for a password and never changes the email address by phone.

The support desk answers messages from Monday to Friday between eight and eighteen local time and on Saturday
between nine and thirteen. Messages that arrive outside those hours are answered on the next opening day, with the
exception of cold-chain damage claims, which are watched by the duty packer until twenty hours on weekdays. The desk
works in the language of the account, and has staff for the three regional languages. Calls are returned within two
working hours, and a member can ask for a callback instead of waiting in the queue.

Members pay an annual contribution that includes two free express deliveries and a ten per cent discount on pallet
freight. The contribution is refunded in full if the member cancels within fourteen days. Membership can be paused
for up to six months, for example during a long absence, and the claims history is kept while it is paused. A member
who has not placed an order in twenty-four months receives a notice, and the account is closed thirty days later
unless the member replies. Closing an account never deletes the invoices, which the cooperative must keep for
ten years, but all other personal data is erased within sixty days of closing.

Group orders for restaurants and shops are handled by the account manager listed on the member's page, who can
agree a standing delivery day, a credit limit and a single monthly invoice. The same damage procedure applies to
group orders, with the difference that the claim deadline for pallet deliveries is seventy-two hours, because
receiving staff often check the goods only on the next working day.
""",
    """# Payments and invoices

The cooperative accepts bank transfer, card and a monthly invoice for approved group accounts. A card is charged
when the parcel is handed to the carrier, not when the order is placed, so that an order that cannot be packed is
never charged. A bank transfer order is held for five working days; if the money has not arrived by then, the order
is cancelled and the member is told by message. Invoices are issued as a document in the account area on the day of
dispatch and are never changed afterwards: a correction is a new document that cancels the old one and creates a
fresh one, so that the accounting trail stays readable. Value added tax is shown on every line, and the invoice
states the dock the goods left from.

Refunds follow the original payment method. A card refund appears on the member's statement in three to five working
days, depending on the bank, and a bank transfer refund is sent on the next payment run, which happens on Tuesday and
Friday. Store credit can be chosen instead of a refund and is applied at once; it does not expire while the
membership is active. A member who disputes a charge with the bank before writing to the support desk delays the
refund, because the cooperative cannot refund a payment that is under dispute until the bank closes the case.

Members who pay by monthly invoice receive one summary on the first working day of the month, listing every order,
every approved claim and every credit. The summary is due in thirty days. A late payment stops new orders until the
balance is cleared, but never stops an open damage claim, which is always processed. Disagreements about an invoice are
raised with the account manager in writing within fourteen days of the summary, quoting the order numbers concerned.
""",
    """# Product notes: oils, vinegars and flour

Olive oil is sold in bottles of half a litre, one litre and three litres, and in crates of six bottles. The oil in
a crate comes from a single harvest and carries a harvest year on the label; mixing years inside a crate is never done.
Cloudy oil at the bottom of a bottle is normal in cold weather and is not a defect: it clears when the bottle is
kept at room temperature. A bottle that smells of vinegar or crayons is a defect and is replaced. Oil should be stored
away from light and heat, and an opened bottle is best used within six months.

Vinegars are sold in bottles of a quarter litre and half a litre and in barrels of twenty litres for shops. A thin
white film on the surface of an opened vinegar is harmless. Barrels travel by pallet carrier and are filled the day
before dispatch to limit the time the contents spend in transit. A barrel that leaks on arrival is a heavy damage
claim and is handled by the carrier's claims team in parallel with the replacement.

Flour is sold in one, five and ten kilogram bags and in twenty-five kilogram sacks. Flour is a dry good and does not
need a cold chain, but a sack that is wet on arrival must be reported within forty-eight hours, because damp flour
spoils quickly. Seasonal products, such as chestnut flour and new-harvest oil, are available in limited quantities and
are shipped in the order they were placed; when a seasonal product runs out, the order is shipped without it and the
price is refunded on the same day.
""",
]


def main() -> None:
    context = PAGE_SEPARATOR.join(p.strip() for p in PAGES)
    messages = answer_question(context, QUESTION)
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    out = Path(__file__).with_name("scoreboard_wiki_prompt.json")
    out.write_text(
        json.dumps(
            {
                "description": "The messages Anthill sends for one wiki question: the answer system message, "
                "the reference block of three synthetic pages, and the question.",
                "built_with": f"anthill.wiki.prompts.answer_question at commit {commit}",
                "question": QUESTION,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            },
            indent=1,
        )
        + "\n"
    )
    print("wrote", out, sum(len(m.content) for m in messages), "characters")


if __name__ == "__main__":
    main()
