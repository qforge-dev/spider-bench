# Review protocol (plan §8, §10F)

1. Version the rubric before labeling.
2. Import evidence (`danger evidence import`): sources need DOI/PMID/ISBN/URL;
   claims link evidence -> taxon + geography + claim text.
3. Draft one Poland-scoped assessment per taxon (`danger assessments import`):
   category + geography + rationale + evidence + reviewer + date required.
4. Conflicting / severe / uncertain assessments -> qualified reviewer(s);
   `medically_significant` (or disputed) needs **two approvals**.
5. Record reviewer identity, decision date, adjudication history via
   `review_events` (append-only; `record_review_event_sqlite` helper).
6. Status flow: `draft -> in_review -> approved|rejected`, `rejected -> draft`,
   `approved -> superseded`. Illegal transitions raise.
7. Never overwrite reviewed records: amend via a new version + review event.

Adjudication (`review.adjudicate`): any reject -> `rejected`; approvals >=
required -> `approved`; any activity -> `in_review`; else `draft`.
