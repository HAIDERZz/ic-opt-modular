# T17.3b — the digest after the first sessions that used it

Status: specification, 2026-09-29. One task, one commit. It amends section 5 of `T17_1_5_SPEC.md`
(`"digest_version": 2`). Implemented 2026-09-29; where the implementation reads a point differently from this text,
section 5.2 of `T17_1_5_SPEC.md` says what it does (2.5: a variable holds every level where `side × weight` reaches 2,
not from a side of 2, which the side, at most 1.6, never is).

## 1. Why

Eight sessions were run in which an agent read a run's digest every 40 points and advised the run with the real
commands (`ic-opt digest`, `ic-opt advise`), on four development circuits. They gave advice that helped, and they
reviewed and revoked advice that did not. What they could not read off the digest, or read wrongly, is this list. Each
item names what the session met.

## 2. Changes

### 2.1 An advice's period
An advice is in effect from its `since` to the `since` of the row that ended it: its `revoke` row, or the next `adopt`
row; to the end of the run while it is in effect. "The others" of an advice are the points proposed at a history size
inside that period that are neither under it (origin suffix) nor its start points.

*Met:* after an advice was revoked, "best of the others since" went on counting the points proposed after the
revocation; a revoked advice was compared with points it never competed with.

`advice[i]` gains `period: [since, until]` (`until`: `null` while in effect). `best_others_since` is replaced by the
entries of 2.2.

### 2.2 Both sides of an advice, counted
Per advice, for the points under it and for the others of its period: points; feasible points; points that gave no
value (status not scored); the best objective (as the spec states it, `null` without a feasible point).

*Met:* an advice meant to keep the run out of a region where simulations fail could only be judged by subtracting the
totals of two digests.

`advice[i].under` and `advice[i].others`: `{points, feasible, no_value, best}`. The keys `points`, `best` and
`others_since` of version 1 go.

### 2.3 Start points of an advice
Per start point: id, status, its objective when it is feasible (`null` otherwise: an infeasible point's objective is
not printed as if it were a result), and `best_then`: whether it was the best feasible point of the run when it was
evaluated.

*Met:* an advice of start rows only showed "points under it 0, best —"; that three of its rows had each become the
run's best was not to be seen. A start point that failed a constraint was printed with a bare number after its status.

### 2.4 The best point at an advice's bound
For the run's best feasible point, when it was proposed under the advice in effect (or lies inside its ranges): the
variables whose level is a bound of the advice's range and not a bound of the spec's range, with the side.

*Met:* in two sessions the final best point lay on the bound the advice had set. Both agents saw it by reading
parameters; the digest did not say it. Beyond that bound there may be better points, and an advice can be replaced by a
wider one.

`advice[i].best_at_bound`: `[{variable, side, value}]`, empty when there is none, `null` when the best point is not
under or inside the advice. Markdown: one line under the table per advice that has one.

### 2.5 The search region's side
State what the number means where it is printed: the region holds, per variable, the levels within `side × weight / 2`
of the centre in unit coordinates, a range being 1 long and the weight between 0.2 and 5
(`suggesters/metric_gp/region.py`); from a side of 2 the region holds every level whatever the centre.

*Met:* "side 1.6 of the unit cube" was read as a contradiction.

### 2.6 What the unscored points said
`failures` gains `messages`: the three most frequent texts among the `issues` of the points that gave no value, with
their counts (texts as they are stored; numbers inside a text are not normalized away).

*Met:* every session asked why a metric gave no value; the digest says where, the observations hold what the simulator
or the expression said.

### 2.7 Operating points
When no observation of the run holds operating points, say that in one sentence instead of the three possible reasons
for the best point alone.

## 3. What does not change
Everything else of section 5: the digest is pure, every number is computed from the observations, the command takes no
lock. The rank correlations stay as they are: the sessions found them unstable from one digest to the next, and the
answer is in the procedure (the skill), not in another number.

## 4. Tests
1. The period: adopt at 40, revoke at 80, points to 120: the others are those of 40–79 only. Adopt a1 at 40, a2 at 80:
   a1's period ends at 80.
2. Both sides counted, on a made-up run with known counts, including an advice under which nothing is feasible.
3. Start points: a feasible one that was the best when evaluated, a feasible one that was not, an infeasible one.
4. The best point at an advice's bound: on the bound; inside; on a bound that is also the spec's; not under the advice.
5. The messages: counts and order; a run without unscored points.
6. A store without advice and a store of 0.4.0 still give a digest (the existing tests).
7. The Markdown of 2.1–2.7 on the made-up run: the table's columns, the line of 2.4, the sentence of 2.5 and of 2.7.

## 5. Documents
`T17_1_5_SPEC.md` section 5.2 (the rows `advice`, `failures`, `strategy`, `operating_points`, the version), README and
the skill where they describe the digest's advice table; the plan's section 7 (a record at the END of the file);
BACKLOG entry N-68 (status text).
