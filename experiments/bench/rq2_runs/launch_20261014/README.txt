CM-3 dropout launch streams (frozen 2026-10-14).
Construction from campaign_v2_master5000_seed42.txt (design b239833 SS5.1 band 4):
  D-B: lines 1911..1975 (W=5: 1911-1915, N=60: 1916-1975) + 5x repeat of lines 1916..1975 = 365
  D-C: lines 1976..2040 (W=5: 1976-1980, N=60: 1981-2040) + 5x repeat of lines 1981..2040 = 365
Fault at request index 65 (after pre-fault pass). Recovery: embedded timed restart
(schtasks /End; sleep 2; /Run) so the K=3 streak completes mid-stream (design SS2.2 R=5 post-failure repeats).
