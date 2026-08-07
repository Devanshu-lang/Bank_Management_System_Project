# Legacy Bank — Premium Digital Banking Suite

<img width="1229" height="398" alt="image" src="https://github.com/user-attachments/assets/d6458c15-1c41-4ff2-8712-10c77218acd0" />
<img width="1061" height="393" alt="image" src="https://github.com/user-attachments/assets/92622ab3-99f4-472b-8b31-f01f68ea9797" />
<img width="1241" height="409" alt="image" src="https://github.com/user-attachments/assets/44b3c6cb-e42a-48a5-b824-051b077e5fd4" />
<img width="1236" height="362" alt="image" src="https://github.com/user-attachments/assets/eddf9c98-def4-4f77-bee2-9acb5da2be7d" />
<img width="1234" height="419" alt="image" src="https://github.com/user-attachments/assets/bb708343-bb25-4ee7-9ee3-1815aecf8724" />
<img width="1256" height="310" alt="image" src="https://github.com/user-attachments/assets/6d4fb071-f563-48dd-8cf6-5f929f8856a5" />
<img width="1245" height="415" alt="image" src="https://github.com/user-attachments/assets/528ea2d9-95dc-4aa9-983a-b46cf6c304a8" />



A dark-luxury Streamlit banking simulator: sign up, sign in, deposit,
withdraw, transfer funds between accounts, and view a full transaction
history — all wrapped in a CRED/Revolut-style glassmorphism UI.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-1.31+-red)
![Status](https://img.shields.io/badge/status-first--project-brightgreen)

## Disclaimer

This is a **portfolio/learning project**, built to practice and demonstrate
production-ready engineering habits — not a real financial product. It is
not affiliated with any bank, is not regulated, and should never be used
with real money, real account numbers, or real personal financial data.
Think of it as a fully working simulation: the code quality is real, the
"bank" is not.

## Live demo

https://bankmanagementsystem12.streamlit.app/

## Features

- **Authentication** — Sign Up / Sign In, session-gated banking pages
- **Core banking** — Deposit, Withdraw, Fund Transfer between accounts
- **Transaction History** — filterable ledger, CSV export, balance-trend chart
- **Profile management** — update name/email/mobile/PIN, close account
- **Security** — salted + hashed PINs (PBKDF2-HMAC-SHA256), duplicate-account
  prevention, per-transaction limits, atomic file writes

## Tech stack

Python 3.10+, Streamlit, Pandas, `hashlib`/`hmac` for credential security.

## Run it locally

```bash
git clone <your-repo-url>
cd <repo-name>
pip install -r requirements.txt
streamlit run app.py
```

---

## How this was built (and what's genuinely mine)

I wrote this last week with guidance from my mentors at **Sheryians Coding
School**, as one of my first real Python projects.

**The Python backend logic is mine**, built with mentor support. Before
there was any UI, I wrote a command-line banking system in plain Python: a
`Bank` class storing account records in JSON, account creation, deposits,
withdrawals, balance checks, profile updates, and account deletion, all
driven by `input()` prompts. That script is what taught me classes,
`classmethod`/`staticmethod`, reading and writing JSON to disk, list
comprehensions for querying records, and basic exception handling.

It also had real bugs — the kind you only really learn from making them:

- `update_details` and `delete_user` were defined without `self`, so
  calling them as `bank.update_details()` would actually crash
- `if user == False:` to check for "no matching account" — a list is never
  equal to `False`, so that check never did what I intended
- Inside `update_details`, the "leave blank to keep your old value" logic
  used `==` instead of `=` (comparison instead of assignment), so it
  silently never actually kept anything
- The save/update logic in `update_details` was indented to run
  unconditionally, even when no matching account was found — which would
  crash trying to read data from an empty result
- `Bank.data.index(user)` in `delete_user` — `user` is a list containing
  the match, not the match itself, so this needed to be
  `Bank.data.index(user[0])`
- `Bank.update()` was called instead of the actual private method
  `Bank.__update()` (which Python name-mangles to `_Bank__update` outside
  the class), so it would have raised an `AttributeError`
- The menu at the bottom called `bank.deleteuser()`, a method that didn't
  exist — the real method was `delete_user`
- PINs were stored as plain integers directly in the JSON file — no
  hashing at all
- Account numbers had no uniqueness check, and nothing stopped the same
  email or mobile number from opening multiple accounts

**The Streamlit frontend was built entirely with Claude.** I had no web UI
at all — just a text menu. I described the banking logic I already had and
worked with Claude to design and build the whole interface from scratch:
the dark-navy/gold theme, the virtual debit card, the dashboard, forms,
alerts, sidebar navigation, and the confirmation dialogs.

**The backend hardening was a collaborative review.** I walked Claude
through my original script, flagged what felt fragile to me, and had it
point out what I'd missed. Together we fixed the bugs above and added:

- Salted PBKDF2-HMAC-SHA256 PIN hashing instead of plaintext storage
- Atomic file writes (temp file + `os.replace`) so a crash mid-save can't
  corrupt the database
- Duplicate-account prevention and guaranteed-unique account numbers
- New features I hadn't originally built: Fund Transfer between accounts
  and a full Transaction History view with filtering and CSV export

## What I learned

**Python fundamentals** — this being my first real project, most of what I
learned came directly from writing the original CLI script:

- **Classes and objects** — modeling a `Bank` as a class, with account
  records as dictionaries inside it
- **`classmethod` vs. `staticmethod`** — when a method needs access to
  shared class data (`cls.data`) versus when it's just a standalone utility
  (like generating an account number)
- **Name mangling** — what Python actually does with double-underscore
  method names like `__update`, and why calling `Bank.update()` from
  outside the class doesn't work the way you'd expect
- **File I/O and JSON** — reading and writing structured data to disk with
  `open()`, `json.load()`, and `json.dumps()`
- **List comprehensions** — filtering my in-memory account list to find a
  matching user by account number and PIN
- **The `random` and `string` modules** — generating a random account
  number from letters and digits
- **Exception handling** — wrapping risky operations (like reading a file
  that might not exist, or converting input to an integer) in `try`/`except`
- **Type casting and input validation** — converting `input()` strings to
  `int` where needed, and checking things like PIN length before accepting
  them
- **Truthiness vs. equality** — the hard way, via my own `if user == False`
  bug. A list is never equal to `False`; what I actually wanted was
  `if not user`

**Software engineering practices** — these came from the review pass with
Claude, applied on top of the Python I already knew:

- Why storing credentials in plaintext is a real security problem, and
  what salted hashing (PBKDF2-HMAC-SHA256) actually does about it
- What atomic writes are and why flat-file "databases" need them
- How to structure a growing codebase instead of one long script
- How to validate input consistently across every form instead of
  re-checking the same rules in different places

## A bug I caught after deploying the demo

Fixing the original script wasn't the end of it. After deploying `app.py`
to Streamlit Cloud as a live demo, the app crashed with a `KeyError` the
moment someone tried to sign up. I didn't just accept that and move on — I
dug into it, traced it back to a leftover account record in
`database.json` from an earlier version of the app that didn't have all
the expected fields, and had it fixed so the app now skips any malformed
record instead of crashing on it. Catching and diagnosing that — instead
of just restarting the app and hoping it went away — is the part I'm most
proud of from this whole project.

## What I'd do differently for real production use

`database.json` is a flat file — fine for learning and demos, not for real
banking data. The storage functions are written so they could be swapped
for a real database (Postgres, etc.) without touching the rest of the app —
that's my next thing to learn and try.

## Acknowledgments

Thank you to my mentors at **Sheryians Coding School** for the guidance that
made the original backend logic possible — this project wouldn't exist
without that foundation.

## License

MIT — a standard open-source license meaning anyone can use, copy, or
modify this code freely, with no warranty. Just don't put real money
through it.
