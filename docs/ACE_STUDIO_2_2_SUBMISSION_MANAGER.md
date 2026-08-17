# ACE Studio 2.2 — Submission Manager

The Submission Manager separates simulation from BRAIN submission.

## Workflow

1. Simulate alphas.
2. Run BRAIN production-correlation verification from Results & Analytics.
3. Open Submission Manager.
4. Review candidates ranked by a transparent local score.
5. Queue selected alphas.
6. Manually submit from the queue.
7. ACE tracks the local daily quota and leaves overflow queued for later.

## Safety

Passing the simulation gate does not automatically submit an alpha. An alpha must have:
- a BRAIN alpha ID;
- passed the stored simulation gate;
- a verified production correlation below 0.70.

The default local quota is 4/day and the timezone is Africa/Lagos. Both are configurable because server-side BRAIN limits can vary. The local quota is only a guard and does not override BRAIN.

## Submission states

`queued` → waiting for an available slot.

`submitting` → ACE is making the BRAIN request.

`submitted` → BRAIN accepted the submission request.

`error` → BRAIN rejected/failed the request; the record can be retried.

Submitted records remain in the local history and cannot be deleted.

## Important

ACE does not claim that a local quota equals the platform's current quota. If BRAIN rejects a request because of a server-side limit, ACE records the error and does not consume another local slot.
