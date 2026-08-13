# Market-signals cache strategy validation: 2026-06

- Source: local `page_html_cache` market_signals snapshots.
- Unit: one strategy/race = 100 yen. Multiple combinations under the same strategy/race are treated as one ticket group.
- Filter: `p1>=0.55` means the model's top first-place probability is at least 55%.
- Caveat: this validates saved June market-signal snapshots, not strategies added after June that were never present in those snapshots.

## Overall

| Filter | Result |
|---|---:|
| all cached MD/adopted signals | n=10 hit=30.0% ROI=308.0% profit=+2,080 |
| p1>=0.55 | n=7 hit=14.3% ROI=54.3% profit=-320 |

## By Strategy

| Strategy | Base | p1>=0.55 | Change |
|---|---:|---:|---:|
| 尼崎 1-3 (`amagasaki_13_exa`) | n=5 hit=40.0% ROI=132.0% profit=+160 | n=3 hit=33.3% ROI=126.7% profit=+80 | hit -6.7pt / ROI -5.3pt |
| 蒲郡 1-3-2 潮型 (`gamagori_tide_132_tri`) | n=1 hit=100.0% ROI=2420.0% profit=+2,320 | n=0 hit=0.0% ROI=0.0% profit=+0 | hit -100.0pt / ROI -2420.0pt |
| 蒲郡 1-2-3 実戦型 (`gamagori_123_general_practical_tri`) | n=1 hit=0.0% ROI=0.0% profit=-100 | n=1 hit=0.0% ROI=0.0% profit=-100 | hit +0.0pt / ROI +0.0pt |
| 下関 1-2-3 (`shimonoseki_123_tri`) | n=1 hit=0.0% ROI=0.0% profit=-100 | n=1 hit=0.0% ROI=0.0% profit=-100 | hit +0.0pt / ROI +0.0pt |
| 常滑 1-2 (`tokoname_12_exa`) | n=1 hit=0.0% ROI=0.0% profit=-100 | n=1 hit=0.0% ROI=0.0% profit=-100 | hit +0.0pt / ROI +0.0pt |
| 津 1-2-3 (`tsu_123_tri`) | n=1 hit=0.0% ROI=0.0% profit=-100 | n=1 hit=0.0% ROI=0.0% profit=-100 | hit +0.0pt / ROI +0.0pt |

## Raw

- Cache picks rows: 20
- Ticket groups: 10
- p1>=0.55 ticket groups: 7