CM-3 dropout launch streams (frozen 2026-10-14).
Construction from campaign_v2_master5000_seed42.txt (design b239833 SS5.1 band 4):
  D-B: lines 1911..1975 (W=5: 1911-1915, N=60: 1916-1975) + 5x repeat of lines 1916..1975 = 365
  D-C: lines 1976..2040 (W=5: 1976-1980, N=60: 1981-2040) + 5x repeat of lines 1981..2040 = 365
Fault at request index 65 (after pre-fault pass). Recovery: embedded timed restart
(schtasks /End; sleep 2; /Run) so the K=3 streak completes mid-stream (design SS2.2 R=5 post-failure repeats).

FAULT MECHANISM (validated by rehearsal): bash-native async pair from Node A:
  ssh laptop "schtasks /End /TN pdc_gateway" & sleep 4; ssh laptop "schtasks /Run /TN pdc_gateway" & wait
Returns in ~5s; measured outage ~17s (B restart ~10s after /Run); stream samples the
window (paced errors) then the K=3 streak completes mid-stream.

UPDATE 2026-10-15 (pre-v8): restart delay raised 4s -> 45s in BOTH fault scripts.
Rationale: in v6 the ~20s outage was bridged by TCP SYN retry - the in-flight
request completed ok in 20.6s, ZERO errors, leaving the during_fault stratum
empty. A 45s restart delay (outage ~= 45s + gateway restart time) exceeds the
30s request timeout, so in-flight requests genuinely error and during_fault
carries both ~30000ms timeouts and hard errors. Supersedes the FAULT MECHANISM
timing note above.
