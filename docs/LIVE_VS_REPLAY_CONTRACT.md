# Live versus replay — review contract

BAR_CLOSED: source-series completion, separately evidenced. Current position alone is insufficient. The prototype requires stable identical content in successive reads, a later source bar, strictly increasing source timestamps and a tip consistent with fresh ticks. This proves relative closure under the trusted MT5 source contract; it does not prove old UTC labels.

DATA_AVAILABLE_NOW: returned exact content is available at the externally bounded receipt cutoff, and the capture remains fresh for present analysis. Does not mean it existed at its original market-event time.

HISTORICALLY_AVAILABLE_AT: this prototype records only the conservative receipt timestamp at which exact content is demonstrated to exist. Earliest availability before capture remains UNKNOWN. A replay with an earlier cutoff is denied. A later replay may use that version after its proven receipt, subject to all other dependencies and correct time semantics.

Current analysis must not demand original historical availability. It must still distinguish source order, UTC/session time, derived-feature establishment and decision time. No score is a permission. No automatic synthetic/recorded conversion.

The new function is pure and does not persist global earliest-seen history, publish scenarios or update the journal. Its first-observed field is capture-local. Existing immutable receipts remain the audit source. A production consumer must bind source identity, instrument, timeframe, raw epoch, content hash and earliest verified receipt; revisions must not reuse the prior version's availability.

Legacy UTC/session admission remains false. No field called utc_time is populated with guessed source-local labels. The package is a contract/prototype step and must not be deployed as a completed restoration.
