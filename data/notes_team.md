# Kickoff - 2026-09-03

Attendees: Divya Menon, Meera Joshi, Tanvi Shetty, Sameer Kulkarni

## Decisions
- Stack: FastAPI + managed Postgres 15. Reason: the team knows Python, and the POS webhook and staff app write concurrently.
- Meera owns the notification service (notify/ package).
- Tanvi owns the staff app.
- Divya owns production deploys and access grants (SSH, cloud console).

## Open
- Pilot restaurant: Saffron Lane Kitchen is confirmed. Greenleaf Cafe is "maybe in December".
- On-call during the pilot: TBD.

# Sprint review - 2026-10-21

Attendees: Divya Menon, Meera Joshi, Tanvi Shetty

## Outcomes
- Textly took 9 days to approve one template. Decision: drop Textly, move SMS to Pingwire (pre-approved DLT route, 1-day approval in the trial).
- The Textly adapter stays in the repo until 2026-11-21 for rollback, then it is deleted.

## Action items
- Meera: migrate the notifier to Pingwire, due 2026-10-25.

## Retry queue
- Agreed on option B for the retry queue. Meera to implement.

# Handover sync - 2026-11-10

Attendees: Divya Menon, Meera Joshi, Arjun Rao

## Covered
- Arjun takes over the notify/ package and the reconcile job from 2026-11-09.
- Reconcile job: known to fail on the first Monday of each month when the gateway settlement file is late. Workaround: rerun with --wait-for-file 90.
- Divya will rotate the Pingwire API key on Meera's last day (T-17).

## Not covered yet
- Pingwire dashboard access for Arjun.
- Who covers on-call after 2026-11-27.
- Migration 0007 for export-v2 is still to be applied on staging. Meera to do it before the 2026-11-18 demo.
