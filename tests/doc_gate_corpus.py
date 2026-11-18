"""The documents and questions the relevance gate of the document passages was measured on (issue #226, step 2).
Written by hand: four short documents, 16 questions whose answer is in one of them, 10 that no document answers."""

DOCS = {
    "hollis-bakery-handbook.pdf": """Hollis Bakery: operations handbook

Hollis Bakery was founded in 2011 by Marta Hollis in the Ostend quarter. It employs fourteen people and bakes about nine hundred loaves a day.

The bakery opens to customers at 6:30 in the morning and closes at 4 in the afternoon, Tuesday to Saturday. It is closed on Sundays and Mondays.

Production starts at 2 a.m. The head baker, Jonas Reuter, is responsible for the sourdough starter, which is fed twice a day and kept at 24 degrees.

---

Suppliers and deliveries

Flour comes from the Brandt mill, delivered every Tuesday and Friday before 5 a.m. The standing order is 1,200 kilograms of wheat flour and 300 kilograms of rye.

Butter and dairy come from the Wiesenhof cooperative. Orders must be placed by Wednesday noon for the Thursday delivery, otherwise the price rises by eight percent.

The delivery van is a white Citroen Jumper. Its licence plate is ZX-4417 and it is serviced every ten thousand kilometres at the Kessler garage.

---

Refunds, allergens and incidents

Customers can return unopened packaged goods within 45 days for a full refund. Fresh bread cannot be returned, but it is replaced on the same day if it is faulty.

All products are baked in a facility that handles nuts, sesame and gluten. The allergen sheet is updated every March and kept at the till.

On 14 March 2026 the walk-in cooler failed overnight and 220 kilograms of dough were discarded. The repair invoice number is 8820-B and the insurer reimbursed 3,450 euros.""",
    "riverside-cycling-club.html": """Riverside Cycling Club: member handbook

The club was founded in 1998 and has 212 members. Rides leave from the boathouse car park at 8:00 on Saturdays and 18:30 on Wednesdays in summer.

Membership costs 85 euros a year for adults and 40 euros for under-18s. New members get a free club jersey after their third ride.

Ride groups are graded A to D. Group A averages 32 kilometres an hour, group B 27, group C 23 and group D 18. Nobody is dropped: the group regroups at every junction marked with a yellow flag.

Safety rules: helmets are compulsory, rear lights are required after sunset, and riders must not exceed two abreast on the Mill Road section. The club insurance policy number is RCC-77120 with the Alderney mutual.

The annual training camp takes place in Mallorca during the second week of April. The deposit is 150 euros and the coach, Ines Albers, closes bookings on 28 February.""",
    "tenancy-summary.pdf": """Tenancy agreement summary: 14 Linden Row

The tenant is Samira Okafor. The landlord is Weller Property Ltd. The lease starts on 1 September 2025 and runs for 24 months.

---

Rent is 1,180 euros a month, due on the third working day. The deposit is two months of rent, held in the tenancy deposit scheme under reference TD-55931.

The tenant is responsible for utilities and the internet connection. The landlord provides the boiler service every autumn.

---

The notice period is three months for the tenant and six months for the landlord. Pets are allowed with written consent. Subletting is not allowed. Rent increases are capped at 3 percent per year and need two months of written notice.

Maintenance requests go to the agent, Henrik Dalgaard, on 0151 555 0142. Emergency repairs outside office hours go to the 24 hour line.""",
    "clinic-policy.md": """# Harbour Street Clinic: patient policy

Appointments can be cancelled free of charge up to 24 hours before the start. A late cancellation is charged 25 euros.

## Prescriptions

Repeat prescriptions take three working days. The pharmacy partner is Nordlicht Apotheke on Marktplatz. Controlled medicines need a fresh consultation every 28 days.

## Records

Patient records are kept for ten years after the last visit. A copy can be requested in writing and is sent within 14 days. Requests from a third party need the patient's signed consent.

## Complaints

Complaints go to the practice manager, Dr. Lena Vogt, who replies within 10 working days. The clinic's data protection officer is Omar Siddiqui.""",
}
QA = [
    ("Which mill does Hollis Bakery buy its flour from?", "Brandt mill"),
    (
        "By how much does the butter price rise if the order is placed after Wednesday noon?",
        "eight percent",
    ),
    ("What is the licence plate of the delivery van?", "ZX-4417"),
    ("What is the repair invoice number for the cooler failure?", "8820-B"),
    ("How much does adult membership of the cycling club cost per year?", "85 euros"),
    ("What is the club insurance policy number?", "RCC-77120"),
    ("When does the cycling club training camp deposit get due and how much is it?", "150 euros"),
    ("How fast does ride group B average?", "27"),
    ("What is the tenant's deposit reference?", "TD-55931"),
    ("How long is the notice period for the landlord?", "six months"),
    ("Who do I call for maintenance requests on the tenancy?", "Henrik Dalgaard"),
    ("By how much can the rent be increased per year?", "3 percent"),
    ("How much is a late cancellation charged at the clinic?", "25 euros"),
    ("How long does a repeat prescription take?", "three working days"),
    ("Who is the data protection officer of the clinic?", "Omar Siddiqui"),
    ("How long are patient records kept?", "ten years"),
]
UNRELATED = [
    "What is the capital of France?",
    "Write me a short poem about the sea.",
    "How do I reverse a list in Python?",
    "Hello, how are you today?",
    "What is the boiling point of water at sea level?",
    "Explain what a mortgage is.",
    "Who painted the Mona Lisa?",
    "Give me three tips for a job interview.",
    "What is the difference between a virus and a bacterium?",
    "Translate good morning into Spanish.",
]
