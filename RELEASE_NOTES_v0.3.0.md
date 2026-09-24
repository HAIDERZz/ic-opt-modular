# IC-Opt 0.3.0

Two things happened since 0.2.0. The device query library became part of the
product: a directory of EM run stores plus a manifest answers "what does this
geometry give", "which geometries give this" and "which region of the geometry
space gives this", with calibrated intervals and real-EMX verification. And
every machine-specific assumption left the code: limits per host, resources
stated by the user, identities that hold no machine facts, a controller that
can be Windows or macOS.

## Breaking changes and how to upgrade

Do the steps in this order; each one is checked by the tool.

1. **`~/.ic-opt/site.yaml` is per host and required.** Version 2 keeps one
   entry per host under `hosts:` — `local` for the machine running ic-opt and
   one per `--ssh-profile`:

   ```yaml
   hosts:
     local: {max_threads: 16, max_memory_gb: 32, cshrc: /path/to/env.csh}
     lab:   {max_threads: 64, max_memory_gb: 128, cshrc: /path/on/lab/env.csh,
             scratch_root: /scratch/me/ic-opt, license_probe: lmstat -a, transfer_timeout_s: 1800}
   ```

   `max_threads` and `max_memory_gb` have no defaults: without the entry
   nothing starts (`SiteError`), and a spec whose stages need more than the
   entry is refused before anything runs (`EnvelopeError`). A flat 0.2.0 file
   (top-level `max_threads`) is read as `hosts.local` with a note to move it.
   `hosts.local` also budgets the library's own compute (model fits, BLAS
   threads, prediction chunks).

2. **Resources are stated in the spec.** `simulator.threads_per_run`,
   `em.threads`, `em.memory_gb` and `em.timeout_s` are required. The values
   0.2.0 defaulted to were 10 / 4 / 32 / 3600; write the values you actually
   ran with **before** step 3, because the old stamps hash them.

3. **Identities changed; restamp your stores.** An observation's spec
   fingerprint now hashes the problem only (no jobs, threads, timeouts,
   retention, budget); an EMX stage's identity — a library part's generation —
   hashes the process file's content on the simulation host, not its path.
   `ic-opt migrate-store PROJECT [--ssh-profile P] [--dry-run]` restamps a
   project or a library part in place (backup first, idempotent), moves EMX
   cache entries to their new keys and repoints `library.yaml` pins. It
   matches rows stamped by 0.2.0's own formulas too (its spec hash and its
   stage-name pipeline hash), and a 0.2.0 store loads again in the first
   place: 0.2.0 wrote a child's unit as `testbench`, which the development
   line had renamed, so every command on such a store used to fail. Rows
   migrate-store cannot match are reported and left as they are: they are not
   reused and still count against the budget.

4. **The 0.1 command line is gone.** `ic-opt PROJECT --real|--doctor|--continue N`
   was kept for one release, as 0.2.0 said. Run `ic-opt migrate PROJECT` once
   and then `ic-opt run RECIPE PROJECT ...`; the old form prints exactly that.

5. **Install in two steps.** `pip install -e vendor/open-box -e vendor/TuRBO`
   then `pip install -e .`. TuRBO (the default fine-stage strategy of
   `lib_design`, `lib_signoff` and `coarse_to_fine`) is under Uber's
   non-commercial licence and is not in the wheel; without it `turbo` reports
   `turbo_missing`. scikit-learn and threadpoolctl are declared dependencies.
   `scripts/check_clean_install.sh` builds two clean environments and runs the
   packaging checks.

## New

- **Device query library** (`ic_opt.library`; `docs/em/library.md`). A
  `library.yaml` at the root names the strata (one device family on one metal
  body), their run-store parts, dims and quantities (scalars and anchored
  curves such as `Lp@40`). Datasets are re-measured from the stored sNp under
  one definition (peaks below the system SRF, anchors usable only when the
  resonance lies above `srf_margin` × f0), cached by content. One Gaussian
  process per (stratum, quantity) with held-out calibration, a sigma floor at
  the held-out median error and a convex-hull domain guard. Blocks:
  `lib.load`, `lib.coverage`, `lib.query`, `lib.suggest`, `lib.region`
  (target windows → sweep ranges, two feasibility levels), all strict JSON;
  recipes `lib_design` (surrogate-guided design) and `lib_signoff` (candidates
  through real EMX, compared with the predictions, adopted into the library on
  request); the `Predict` stage. Fitted models are cached on disk and fitted in
  parallel processes within `hosts.local`.
- **Process profiles as data.** `em.validate_profile` checks a profile against
  the EMX process file (GDS layers against `define`d layers, pin layers);
  `skills/author-process-rule` is the authoring guide; metals are indexed by
  the profile's stack, the ground fixture's conductor is `fixture_conductor`,
  and the low-frequency band of `L_lf` / `k_lf` is `low_freq_max_hz` (a
  number, or `"relative"` = min(3 GHz, SRF/10)) in the spec or the manifest.
- **Controller portability.** A portable file lock (`fcntl` / `msvcrt`),
  spawned worker processes on every platform (scripts that reach a fit keep
  their top level under `if __name__ == "__main__":`), UTF-8 file IO and
  Windows-safe paths. Windows / macOS controllers are supported by design and
  tested with stubs; not yet on real machines.
- `env.doctor` prints the host envelope and compares it with `nproc` /
  `MemTotal` (a warning, never a block); `migrate-store` for stores; every
  recipe passes `limits=run.limits` to `sim.evaluate` and `opt.optimize`.
- **No private process facts in the package.** The wheel, the sdist and the
  repository were audited for the layer numbers, rule values and file names of
  the private process the pcell port was first built against
  (`docs/refactor/N28_DESENSITISATION_AUDIT_2026-09-25_CN.md`); the shipped
  demonstrations (`_pcell_demo.generate_all`) run on the bundled `demo_6m`
  profile, and the pcell tests read a private profile's values from that
  profile when `IC_OPT_PROFILE_DIRS` names one, else skip.

## Verified

<!-- fill at release: test counts, clean installs, migration of the N28 library, sign-offs -->

## Compatibility

- Stores written by 0.2.0 and by the 0.2.x development line are restamped by
  `migrate-store`; nothing is reused silently across a changed identity.
- `library.yaml` schema `ic-opt-library-v1` is unchanged; `low_freq_max_hz` is
  optional.
- `ic-opt migrate` (0.1 `opt_requirement.md` → `spec.yaml`) stays.
